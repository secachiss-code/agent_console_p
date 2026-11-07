"""A UI-owned daemon restarts after SIGKILL; existing hook credentials still work."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from aconsole.contracts.app import (
    CloseTabParams,
    CloseTabResult,
    ProjectAddParams,
    ProjectView,
    TabIdParams,
    TabView,
    TerminalLaunchSpec,
)
from aconsole.contracts.common import TabStatus
from aconsole.core.bootstrap import _read_recovery
from aconsole.core.infrastructure.http.auth import Decision
from aconsole.ui.client.core_client import CoreClient, core_paths, lock_held
from tests.core.test_claude_mcp import _Mcp
from tests.core.test_wire import (
    _FAKE_PODMAN,
    _connect,
    _daemon,
    _git_repo,
    _open,
    _post_hook,
    _stop,
)


async def _restored(client: CoreClient, previous: int, tab_id: str) -> None:
    while True:
        snapshot = client.store.snapshot
        if (client.daemon_pid not in {None, previous} and not client.store.reconnecting and snapshot is not None
                and any(str(tab.id) == tab_id for tab in snapshot.tabs)):
            return
        await asyncio.sleep(0.02)


def test_ui_restarts_a_killed_core_and_existing_hooks_reach_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FAKE_HOME", str(home))
    podman = tmp_path / "podman"
    _ = podman.write_text(_FAKE_PODMAN, encoding="utf-8")
    podman.chmod(0o700)
    with tempfile.TemporaryDirectory(prefix="ac-recover-") as directory:
        runtime = Path(directory)
        socket, _lock = core_paths(runtime)
        socket.parent.mkdir()
        script = (
            "import asyncio,sys; from pathlib import Path; "
            "from aconsole.core.bootstrap import Daemon,run_with_signals; "
            "from aconsole.core.infrastructure.tabs import HostTabs,HostBins; "
            "root=Path(sys.argv[1]); sock=Path(sys.argv[2]); "
            "host=HostTabs(bins=HostBins(podman=root/'podman'),socket_path=str(sock),user='sas',"
            "worktrees_dir=root/'wt'); "
            "core=Daemon(sock,database=root/'aconsole.db',host=host,idle_s=60); "
            "core.serve_ingest=True; asyncio.run(run_with_signals(core))"
        )
        argv = [sys.executable, "-c", script, str(tmp_path), str(socket)]

        async def scenario() -> None:
            client = CoreClient(str(socket))
            transitions = []
            _ = client.store.subscribe_connection(lambda store: transitions.append(store.reconnecting))
            try:
                _ = await client.start_daemon(argv, runtime=runtime)
                _ = await client.pull_snapshot()
                project = await client.request("project/add", ProjectAddParams(root=str(repo)), ProjectView)
                tab = await client.request("tab/open", _open(project.id), TabView)
                spec = await client.request("tab/launchSpec", TabIdParams(tab_id=tab.id), TerminalLaunchSpec)
                transitions.clear()
                previous = client.daemon_pid
                assert previous is not None
                os.kill(previous, signal.SIGKILL)
                await asyncio.wait_for(_restored(client, previous, str(tab.id)), timeout=10)
                assert transitions == [True, False]
                code = await asyncio.to_thread(
                    _post_hook, int(spec.env["ACONSOLE_PORT"]), str(tab.id),
                    spec.env["ACONSOLE_TAB_TOKEN"], "UserPromptSubmit",
                )
                assert code == 200
                for _ in range(100):
                    snapshot = client.store.snapshot
                    if snapshot is not None and snapshot.tabs[0].status is TabStatus.WORKING:
                        break
                    await asyncio.sleep(0.02)
                assert snapshot is not None
                assert snapshot.tabs[0].status is TabStatus.WORKING
                assert spec.env["ACONSOLE_TAB_TOKEN"].encode() not in (tmp_path / "aconsole.db").read_bytes()
            finally:
                await client.close()

        asyncio.run(scenario())


def test_restart_keeps_the_endpoint_and_token_without_relaunching_a_tab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FAKE_HOME", str(home))

    from aconsole.core.infrastructure.agents.claude.sessions import project_key
    from tests.core.test_claude_sessions import _write

    # _post_hook reports session "s"; token recovery must resume a written conversation.
    transcript = home / ".claude" / "projects" / project_key(str(repo)) / "s.jsonl"
    transcript.parent.mkdir(parents=True)
    _write(transcript, "s", "recovery fixture", cwd=str(repo), mtime=1700000000)

    async def scenario() -> None:
        daemon = _daemon(tmp_path)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            tab = await client.call("tab/open", _open(project.id), TabView)
            spec = await client.call("tab/launchSpec", TabIdParams(tab_id=tab.id), TerminalLaunchSpec)
            port = daemon.ingest_port
        finally:
            await _stop(daemon, task, client)
        daemon = _daemon(tmp_path)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            assert daemon.ingest_port == port
            code = await asyncio.to_thread(
                _post_hook, port, str(tab.id), spec.env["ACONSOLE_TAB_TOKEN"], "Stop",
            )
            assert code == 200
            mcp = _Mcp({
                "url": f"http://127.0.0.1:{port}/mcp",
                "headers": {"Authorization": "Bearer ${ACONSOLE_TAB_TOKEN}"},
            }, spec.env)
            await asyncio.to_thread(mcp.start)
            listed = await asyncio.to_thread(mcp.request, "tools/list", {})
            assert len(listed["tools"]) == 12
            again = await client.call("tab/launchSpec", TabIdParams(tab_id=tab.id), TerminalLaunchSpec)
            assert again.env["ACONSOLE_TAB_TOKEN"] != spec.env["ACONSOLE_TAB_TOKEN"]
            header = "Bearer " + spec.env["ACONSOLE_TAB_TOKEN"]
            assert daemon._tokens.check(header, str(tab.id)) is Decision.UNAUTHORIZED
            assert daemon._mcp_tokens.resolve(header) is None
            assert _read_recovery(daemon._recovery_path).tokens[str(tab.id)] == again.env["ACONSOLE_TAB_TOKEN"]
            closed = await client.call("tab/close", CloseTabParams(tab_id=tab.id, decision="keep"), CloseTabResult)
            assert closed.closed
            assert str(tab.id) not in _read_recovery(daemon._recovery_path).tokens
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_core_lock_is_not_released_while_a_tab_worker_still_runs(tmp_path: Path) -> None:
    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        entered, release = threading.Event(), threading.Event()

        def work() -> None:
            entered.set()
            assert release.wait(2)

        worker = daemon._worker
        assert worker is not None
        _ = worker.submit(work)
        assert await asyncio.to_thread(entered.wait, 1)
        daemon.request_stop()
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout=0.15)
            assert lock_held(daemon.lock_path)
        finally:
            release.set()
            await _stop(daemon, task, client)
        assert not lock_held(daemon.lock_path)

    asyncio.run(scenario())


@pytest.mark.parametrize("unsafe", ["symlink", "permissions", "directory", "oversize", "port"])
def test_recovery_rejects_unsafe_runtime_credentials(tmp_path: Path, unsafe: str) -> None:
    path = tmp_path / "recovery.json"
    path.write_text('{"port":12345,"tokens":{"tab":"secret"}}')
    path.chmod(0o600)
    if unsafe == "symlink":
        target = tmp_path / "target"
        path.rename(target)
        path.symlink_to(target)
    elif unsafe == "permissions":
        path.chmod(0o644)
    elif unsafe == "directory":
        path.unlink()
        path.mkdir()
    elif unsafe == "oversize":
        path.write_bytes(b" " * 262145)
    elif unsafe == "port":
        path.write_text('{"port":65536}')
    with pytest.raises((OSError, ValueError)):
        _ = _read_recovery(path)


def test_recovery_writes_private_file_without_following_a_symlink(tmp_path: Path) -> None:
    from aconsole.core.bootstrap import _Recovery, _write_recovery

    target = tmp_path / "target"
    target.write_text("untouched")
    path = tmp_path / "recovery.json"
    path.symlink_to(target)
    _write_recovery(path, _Recovery(port=12345, tokens={"tab": "secret"}))
    assert target.read_text() == "untouched"
    assert not path.is_symlink()
    assert path.stat().st_mode & 0o777 == 0o600
    assert _read_recovery(path).tokens == {"tab": "secret"}


def test_core_keeps_its_lock_until_a_slow_writer_has_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aconsole.core.infrastructure.persistence import writer as writer_module

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        monkeypatch.setattr(writer_module, "_WAIT_S", 0.03)
        entered, release = threading.Event(), threading.Event()
        writer = daemon._writer
        assert writer is not None

        def work(_session: object) -> None:
            entered.set()
            assert release.wait(2)

        writer.post(work)
        assert await asyncio.to_thread(entered.wait, 1)
        daemon.request_stop()
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout=0.15)
            assert lock_held(daemon.lock_path)
        finally:
            release.set()
            await _stop(daemon, task, client)
        assert not lock_held(daemon.lock_path)

    asyncio.run(scenario())
