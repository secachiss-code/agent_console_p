"""Core test setup: no test touches the real home, and one recording fake for the tab ports."""

from __future__ import annotations

import asyncio
import itertools
import secrets
from typing import TYPE_CHECKING, final

import pytest

from aconsole.core.application.services.tabs import ProjectSeed, TabRequestError, TabService
from aconsole.core.domain.clock import Clock

if TYPE_CHECKING:
    import threading
    from pathlib import Path

    from aconsole.core.bootstrap import Daemon

# Longest a fake `pause` waits on its gate. A test sets the gate long before this.
_GATE_WAIT_S = 5.0
_PATH = "/opt/agent/bin:/usr/bin"

type ExecCall = tuple[str, tuple[str, ...], dict[str, str], str]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The database, sockets, and worktrees of every core test land under `tmp_path`.

    Subprocesses inherit the environment, so `aconsoled --serve` started by a test writes there too.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "xdg-runtime"))


@final
class FakeTabPorts:
    """Recording `TabPorts`. It never signals a process, runs podman, or runs git.

    `pause` waits on `gate` when a test sets one, so a stop wait can be held open on the worker thread.
    """

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.exec_calls: list[ExecCall] = []
        self.ensured: list[str] = []
        self.pauses: list[float] = []
        self.stopped: list[int] = []
        self.scans: list[tuple[str, str]] = []
        self.created: list[tuple[str, str, str, str]] = []
        self.released: list[str] = []
        self.removed: list[tuple[str, str, bool]] = []
        self.state: str = "clean"
        self.orphan_pids: tuple[int, ...] = ()
        self.orphan_error: TabRequestError | None = None
        self.gate: threading.Event | None = None

    def new_id(self) -> str:
        return f"tab-{next(self._ids)}"

    def new_secret(self) -> str:
        return secrets.token_hex(32)

    def pause(self, seconds: float) -> None:
        self.pauses.append(seconds)
        gate = self.gate
        if gate is not None:
            _ = gate.wait(timeout=_GATE_WAIT_S)

    def stop_process(self, pid: int) -> None:
        self.stopped.append(pid)

    def create_worktree(self, repo: str, tab_id: str, slug: str, base: str) -> tuple[str, str]:
        self.created.append((repo, tab_id, slug, base))
        return f"/wt/{tab_id}", f"ac/{slug}-{tab_id}"

    def worktree_state(self, repo: str, dest: str) -> str:
        _ = (repo, dest)
        return self.state

    def release_worktree(self, dest: str) -> None:
        self.released.append(dest)

    def remove_worktree(self, repo: str, dest: str, *, discard_ignored: bool) -> bool:
        self.removed.append((repo, dest, discard_ignored))
        return True

    def ensure_container(self, name: str) -> None:
        self.ensured.append(name)

    def exec_argv(self, name: str, command: tuple[str, ...], env: dict[str, str], workdir: str) -> tuple[str, ...]:
        self.exec_calls.append((name, command, dict(env), workdir))
        return ("/usr/bin/podman", "exec", name, workdir, *command)

    def orphans(self, name: str, tab_id: str) -> tuple[int, ...]:
        self.scans.append((name, tab_id))
        if self.orphan_error is not None:
            raise self.orphan_error
        return self.orphan_pids

    def container_path(self, name: str) -> str:
        _ = name
        return _PATH

    def base_env(self) -> dict[str, str]:
        return {"HOME": "/home/aconsole"}

    def python_bin(self) -> str:
        return "/usr/bin/python3"

    def socket_path(self) -> str:
        return "/run/aconsole.sock"

    def ingest_port(self) -> str:
        return "8765"


def use_fake_tabs(daemon: Daemon, root: Path) -> FakeTabPorts:
    """Give the daemon a tab book on fake ports, keeping its event listener, with project `proj` at `root`.

    The daemon's own tab book runs podman and signals processes. Call this before `serve`: connections read
    `daemon.tabs` when they are accepted.
    """
    ports = FakeTabPorts()
    service = TabService(Clock(), ports, listener=daemon.tabs.listener)
    service.add_project(ProjectSeed(project_id="proj", root=str(root), container="agentbox"))
    daemon.tabs = service
    return ports


async def wait_for_socket(path: Path) -> None:
    """Poll until the daemon has bound its socket. Fails after about a second."""
    for _ in range(100):
        if await asyncio.to_thread(path.exists):
            return
        await asyncio.sleep(0.01)
    message = "socket did not appear"
    raise RuntimeError(message)
