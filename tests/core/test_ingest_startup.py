"""Ingest readiness is the bound server, not the presence of a background task."""

from __future__ import annotations

import asyncio
import socket
import subprocess
from pathlib import Path

import pytest
import uvicorn

from aconsole.contracts.app import OpenTabParams
from aconsole.contracts.common import AgentKind, ProjectId, WorkspaceMode
from aconsole.core.application.services.tabs import ProjectSeed
from aconsole.core.bootstrap import Daemon
from aconsole.core.infrastructure.http.consumer import shutdown_consumer
from tests.core.conftest import wait_for_socket
from tests.socket_paths import short_socket_path

_GIT = "/usr/bin/git"


def test_shutdown_consumer_retrieves_a_failure() -> None:
    asyncio.run(_shutdown_consumer())


def test_bind_failure_explains_itself_and_starts_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asyncio.run(_bind_failure(tmp_path, monkeypatch))


def test_stop_before_readiness_shuts_the_consumer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asyncio.run(_stop_before_ready(tmp_path, monkeypatch))


def test_close_ingest_shuts_a_running_consumer(tmp_path: Path) -> None:
    asyncio.run(_running_consumer(tmp_path))


def test_a_later_tab_hears_why_ingest_is_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asyncio.run(_later_tab(tmp_path, monkeypatch))


async def _shutdown_consumer() -> None:
    async def broken() -> None:
        message = "consumer broke"
        raise RuntimeError(message)

    task = asyncio.create_task(broken())
    await asyncio.sleep(0)
    await shutdown_consumer(task)
    assert task.done()
    assert task.exception() is not None


async def _bind_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _refuse_tcp(monkeypatch)
    daemon = _daemon(tmp_path)
    tab_id = _open_tab(daemon, tmp_path)
    await daemon.open_ingest()
    assert daemon.ingest_serving is False
    assert daemon.ingest_port is None
    assert daemon.ingest_consumer_open is False
    assert "could not bind" in daemon.ingest_detail
    assert daemon.agent_notes == [(tab_id, daemon.ingest_detail)]


async def _stop_before_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def dead(self: uvicorn.Server, sockets: list[socket.socket] | None = None) -> None:
        del self, sockets
        message = "listener closed"
        raise RuntimeError(message)

    monkeypatch.setattr(uvicorn.Server, "serve", dead)
    daemon = _daemon(tmp_path)
    await daemon.open_ingest()
    assert daemon.ingest_serving is False
    assert daemon.ingest_port is None
    assert daemon.ingest_consumer_open is False
    assert "stopped before it was ready" in daemon.ingest_detail
    assert "RuntimeError: listener closed" in daemon.ingest_detail


async def _running_consumer(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    await daemon.open_ingest()
    port = daemon.ingest_port
    assert daemon.ingest_serving is True
    assert daemon.ingest_consumer_open is True
    assert port is not None
    await daemon.close_ingest()
    assert daemon.ingest_serving is False
    assert daemon.ingest_consumer_open is False
    assert daemon.ingest_port is None
    with pytest.raises(ConnectionRefusedError), socket.create_connection(("127.0.0.1", port), timeout=0.2):
        pass


async def _later_tab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _refuse_tcp(monkeypatch)
    repo = _git_repo(tmp_path / "repo")
    sock = short_socket_path(tmp_path / "core.sock")
    daemon = Daemon(sock, database=tmp_path / "aconsole.db", idle_s=30)
    daemon.serve_ingest = True
    task = asyncio.create_task(daemon.serve())
    await wait_for_socket(sock)
    try:
        assert "could not bind" in daemon.ingest_detail
        daemon.tabs.add_project(ProjectSeed(project_id="proj", root=str(repo), container="agentbox"))
        opened = daemon.tabs.open_tab(
            OpenTabParams(
                project_id=ProjectId("proj"),
                agent=AgentKind.CLAUDE,
                workspace=WorkspaceMode.MAIN_CHECKOUT,
                start_now=False,
            ),
        )
        for _ in range(50):
            if daemon.agent_notes:
                break
            await asyncio.sleep(0.02)
    finally:
        daemon.request_stop()
        assert await task == 0
    assert daemon.agent_notes == [(str(opened.id), daemon.ingest_detail)]


def _daemon(tmp_path: Path) -> Daemon:
    daemon = Daemon(short_socket_path(tmp_path / "core.sock"), idle_s=30)
    daemon.serve_ingest = True
    return daemon


def _open_tab(daemon: Daemon, tmp_path: Path) -> str:
    repo = _git_repo(tmp_path / "repo")
    daemon.tabs.add_project(ProjectSeed(project_id="proj", root=str(repo), container="agentbox"))
    opened = daemon.tabs.open_tab(
        OpenTabParams(
            project_id=ProjectId("proj"),
            agent=AgentKind.CLAUDE,
            workspace=WorkspaceMode.MAIN_CHECKOUT,
            start_now=False,
        ),
    )
    return str(opened.id)


def _refuse_tcp(monkeypatch: pytest.MonkeyPatch) -> None:
    real = socket.socket.bind

    def refuse(self: socket.socket, address: tuple[object, ...] | str | bytes | bytearray) -> None:
        if isinstance(address, tuple):
            message = "Address already in use"
            raise OSError(98, message)
        real(self, address)

    monkeypatch.setattr(socket.socket, "bind", refuse)


def _git_repo(root: Path) -> Path:
    root = Path(root)
    root.mkdir()
    _git(root, "init", "-q", "-b", "trunk")
    _ = (root / "README").write_text("hi\n", encoding="utf-8")
    _git(root, "add", "README")
    _git(root, "-c", "user.email=aconsole@localhost", "-c", "user.name=aconsole", "commit", "-q", "-m", "init")
    return root


def _git(root: Path, *args: str) -> None:
    _ = subprocess.run([_GIT, "-C", str(root), *args], check=True, capture_output=True)
