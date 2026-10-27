"""Host adapters for the tab service. No call here runs `distrobox assemble`."""

from __future__ import annotations

import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, final

from aconsole.core.application.services.tabs import TabRequestError, TabService
from aconsole.core.domain.clock import Clock
from aconsole.core.infrastructure.containers.distrobox_env import DistroboxEnvCache, DistroboxError
from aconsole.core.infrastructure.containers.podman import ContainerError, ExecRequest, Podman
from aconsole.core.infrastructure.git.worktrees import WorktreeError, WorktreeRequest, Worktrees

_GIT: Final = Path("/usr/bin/git")
_PODMAN: Final = Path("/usr/bin/podman")
_SHELL: Final = Path("/bin/sh")
_USER: Final = "root"
_PATH: Final = "/usr/bin"
_PORT: Final = "0"
_ID_BYTES: Final = 8
_SECRET_BYTES: Final = 32
_CHECKED_OUT: Final = "already checked out"
_ALREADY: Final = "already exists"
_CREATE_FAILED: Final = "WORKTREE_CREATE_FAILED"
_START_FAILED: Final = "CONTAINER_START_FAILED"
_BRANCH_BUSY: Final = "BRANCH_ALREADY_CHECKED_OUT"


@dataclass(frozen=True, kw_only=True)
class HostBins:
    """Absolute paths of git, podman, and the shell."""

    git: Path = _GIT
    podman: Path = _PODMAN
    shell: Path = _SHELL


@dataclass(frozen=True, kw_only=True)
class HostBox:
    """Optional distrobox ini. PATH is probed only when both paths are set."""

    ini: Path | None = None
    distrobox: Path | None = None


@final
class HostTabs:
    """Worktrees plus podman. `box` is optional so startup does not read a user's distrobox.ini."""

    def __init__(
        self,
        *,
        bins: HostBins | None = None,
        box: HostBox | None = None,
        socket_path: str = "",
        ingest_port: str = _PORT,
        worktrees_dir: Path | None = None,
    ) -> None:
        chosen = HostBins() if bins is None else bins
        linked = HostBox() if box is None else box
        self._trees = Worktrees(chosen.git, shell=chosen.shell)
        self._podman = Podman(chosen.podman)
        if linked.ini is None or linked.distrobox is None:
            self._env = None
        else:
            self._env = DistroboxEnvCache(linked.ini, linked.distrobox)
        self._socket = socket_path
        self._port = ingest_port
        self._worktrees = default_worktrees() if worktrees_dir is None else worktrees_dir

    def new_id(self) -> str:
        return os.urandom(_ID_BYTES).hex()

    def new_secret(self) -> str:
        return os.urandom(_SECRET_BYTES).hex()

    def pause(self, seconds: float) -> None:
        time.sleep(seconds)

    def stop_process(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return

    def create_worktree(self, repo: str, tab_id: str, slug: str, base: str) -> tuple[str, str]:
        root = Path(repo)
        # PLAN §4: `<worktrees_dir>/<repo>/<tab>`, the same path in every agent container.
        dest = self._worktrees / root.name / tab_id
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            created = self._trees.create(WorktreeRequest(repo=root, dest=dest, slug=slug, tab_id=tab_id, base=base))
        except (WorktreeError, ValueError, OSError) as exc:
            raise _create_failed(exc) from exc
        return str(created.dest), created.branch

    def worktree_state(self, repo: str, dest: str) -> str:
        try:
            return self._trees.state(Path(repo), Path(dest))
        except WorktreeError as exc:
            raise TabRequestError(_CREATE_FAILED) from exc

    def release_worktree(self, dest: str) -> None:
        self._trees.release(Path(dest))

    def remove_worktree(self, repo: str, dest: str, *, discard_ignored: bool) -> bool:
        path = Path(dest)
        self._trees.release(path)
        try:
            return self._trees.close(Path(repo), path, discard_ignored=discard_ignored).removed
        except WorktreeError as exc:
            raise TabRequestError(_CREATE_FAILED) from exc

    def ensure_container(self, name: str) -> None:
        try:
            inspected = self._podman.inspect(name)
            if not inspected.running:
                self._podman.start(name)
        except (ContainerError, ValueError) as exc:
            raise TabRequestError(_START_FAILED) from exc

    def exec_argv(self, name: str, command: tuple[str, ...], env: dict[str, str], workdir: str) -> tuple[str, ...]:
        try:
            return self._podman.exec_spec(ExecRequest(name=name, command=command, env=env, workdir=workdir, user=_USER))
        except (ContainerError, ValueError) as exc:
            raise TabRequestError(_START_FAILED) from exc

    def orphans(self, name: str, tab_id: str) -> tuple[int, ...]:
        try:
            return self._podman.orphans(name, tab_id)
        except (ContainerError, ValueError) as exc:
            raise TabRequestError(_START_FAILED) from exc

    def container_path(self, name: str) -> str:
        if self._env is None:
            return _PATH
        try:
            return self._env.path_for(name)
        except DistroboxError as exc:
            raise TabRequestError(_START_FAILED) from exc

    def base_env(self) -> dict[str, str]:
        return dict(os.environ)

    def python_bin(self) -> str:
        return sys.executable

    def socket_path(self) -> str:
        return self._socket

    def ingest_port(self) -> str:
        return self._port

    def set_ingest_port(self, port: int) -> None:
        """The daemon binds the ingest socket after the tab book exists; launches made later carry this port."""
        self._port = str(port)


def default_worktrees() -> Path:
    """`$XDG_DATA_HOME/aconsole/worktrees` (APP_API Settings.worktrees_dir default)."""
    data = os.environ.get("XDG_DATA_HOME", "")
    root = Path(data) if data else Path.home() / ".local" / "share"
    return root / "aconsole" / "worktrees"


def default_tabs(*, socket_path: str = "", ingest_port: str = _PORT, worktrees_dir: Path | None = None) -> TabService:
    """One tab book for the process. Container PATH stays `/usr/bin` until an ini is configured.

    `socket_path` and `ingest_port` reach the sidecar and the agent hooks through the launch environment.
    """
    host = HostTabs(socket_path=socket_path, ingest_port=ingest_port, worktrees_dir=worktrees_dir)
    return TabService(Clock(), host)


def _create_failed(exc: Exception) -> TabRequestError:
    detail = exc.detail if isinstance(exc, WorktreeError) else ""
    blob = f"{exc} {detail}".lower()
    if _CHECKED_OUT in blob or _ALREADY in blob:
        return TabRequestError(_BRANCH_BUSY)
    return TabRequestError(_CREATE_FAILED)
