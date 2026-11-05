"""Host adapters for the tab service. No call here runs `distrobox assemble`."""

from __future__ import annotations

import contextlib
import getpass
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, final

from aconsole.contracts.app import AgentInfo
from aconsole.contracts.common import AgentKind
from aconsole.core.application.services.tabs import TabRequestError, TabService
from aconsole.core.domain.agent import CAPABILITIES
from aconsole.core.domain.agent import AgentKind as DomainAgent
from aconsole.core.domain.clock import Clock
from aconsole.core.infrastructure.agents.cursor.limits import access_token, ask_usage
from aconsole.core.infrastructure.agents.live_limits import ask_rate_limits
from aconsole.core.infrastructure.agents.opencode import limits as opencode_limits
from aconsole.core.infrastructure.clipboard import store_image
from aconsole.core.infrastructure.containers.distrobox_env import DistroboxEnvCache, DistroboxError
from aconsole.core.infrastructure.containers.login import LOGIN_SHELL
from aconsole.core.infrastructure.containers.podman import ContainerError, ExecRequest, Inspected, Podman
from aconsole.core.infrastructure.git.worktrees import WorktreeError, WorktreeRequest, Worktrees
from aconsole.core.infrastructure.host_bins import host_binary

_LOG = logging.getLogger(__name__)
_GIT: Final = host_binary("git")
_GIT_TIMEOUT_S: Final = 10.0
_DEFAULT_BRANCH: Final = "main"
_AGENTS_TTL_S: Final = 30.0
_AGENTS: Final = (AgentKind.CLAUDE, AgentKind.CODEX, AgentKind.OPENCODE, AgentKind.CURSOR)
_DISPLAY: Final = {AgentKind.OPENCODE: "OpenCode"}
# The file each CLI keeps its login in, under the container's home. Only its presence is looked at. OpenCode keeps its
# logins in a database, and `opencode.limits` knows it.
_LOGIN_FILES: Final = {
    AgentKind.CLAUDE: Path(".claude/.credentials.json"),
    AgentKind.CODEX: Path(".codex/auth.json"),
    AgentKind.CURSOR: Path(".config/cursor/auth.json"),
}
_PODMAN: Final = host_binary("podman")
_SHELL: Final = Path("/bin/sh")
_PATH: Final = "/usr/bin"
_PORT: Final = "0"
_ID_BYTES: Final = 8
_SECRET_BYTES: Final = 32
_CHECKED_OUT: Final = "already checked out"
_ALREADY: Final = "already exists"
_CREATE_FAILED: Final = "WORKTREE_CREATE_FAILED"
_IMAGE_PATH_DENIED: Final = "PATH_NOT_ALLOWED"
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

    def __init__(  # noqa: PLR0913 -- host paths and injection points
        self,
        *,
        bins: HostBins | None = None,
        box: HostBox | None = None,
        socket_path: str = "",
        ingest_port: str = _PORT,
        worktrees_dir: Path | None = None,
        user: str | None = None,
    ) -> None:
        chosen = HostBins() if bins is None else bins
        self._git: Path = chosen.git
        linked = HostBox() if box is None else box
        self._trees = Worktrees(chosen.git, shell=chosen.shell)
        self._podman = Podman(chosen.podman)
        if linked.ini is None or linked.distrobox is None:
            self._env = None
        else:
            self._env = DistroboxEnvCache(linked.ini, linked.distrobox)
        # Distrobox maps the host user into the container. As root the agent would have the wrong home and no login.
        self._user = getpass.getuser() if user is None else user
        self._homes: dict[str, Path] = {}
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

    def prepare_image(self, worktree: str, container: str, png: bytes) -> str:
        try:
            path = store_image(worktree, png, git=self._git)
        except (OSError, subprocess.SubprocessError) as error:
            raise TabRequestError(_IMAGE_PATH_DENIED) from error
        try:
            _ = self._podman.run_in(container, self._user, ("test", "-r", str(path)))
        except ContainerError as error:
            path.unlink(missing_ok=True)
            raise TabRequestError(_START_FAILED) from error
        return str(path)

    def stop_in_container(self, name: str, pid: int) -> None:
        with contextlib.suppress(ContainerError, ValueError):
            _ = self._podman.run_in(name, self._user, ("kill", "-TERM", str(pid)))

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

    def agent_output(self, name: str, command: tuple[str, ...]) -> bytes:
        """Stdout of an agent command in a running container, from its first `{`: a login shell may print first."""
        try:
            text = self._podman.run_in(name, self._user, (*LOGIN_SHELL, *command))
        except (ContainerError, ValueError):
            _LOG.warning("%s did not answer in %s", command[0], name, exc_info=True)
            return b""
        start = text.find("{")
        return text[start:].encode() if start >= 0 else b""

    def claude_usage(self, name: str) -> str:
        """What `/usage` prints in the container's Claude. No model call. Empty when it is stopped or silent."""
        command = ("claude", "-p", "/usage", "--no-session-persistence")
        try:
            if not self._podman.inspect(name).running:
                return ""
            return self._podman.run_in(name, self._user, (*LOGIN_SHELL, *command))
        except (ContainerError, ValueError):
            _LOG.warning("claude /usage did not answer in %s", name, exc_info=True)
            return ""

    def codex_rate_limits(self, name: str) -> bytes:
        """The `account/rateLimits/read` reply of the container's Codex. Empty when it is stopped or silent."""
        try:
            if not self._podman.inspect(name).running:
                return b""
            argv = self._podman.pipe_argv(name, self._user, (*LOGIN_SHELL, "codex", "app-server"))
        except (ContainerError, ValueError):
            return b""
        return ask_rate_limits(argv)

    def cursor_usage(self, name: str) -> tuple[bytes, bytes, bytes]:
        """Cursor's usage, plan and per-model replies for the container's account. Empty when it cannot ask."""
        home = self.agent_home(name)
        token = None if home is None else access_token(home)
        if token is None:
            return b"", b"", b""
        return ask_usage(token)

    def opencode_usage(self, name: str) -> bytes:
        """OpenCode Go's usage reply for the container's login. Empty when there is no key to ask with."""
        home = self.agent_home(name)
        key = None if home is None else opencode_limits.access_key(home)
        if key is None:
            return b""
        return opencode_limits.ask_usage(key)

    def inspect_container(self, name: str) -> Inspected:
        return self._podman.inspect(name)

    def agent_home(self, name: str) -> Path | None:
        """The directory HOME names in the container. Distrobox binds it at the same path here."""
        cached = self._homes.get(name)
        if cached is not None:
            return cached
        try:
            inspected = self._podman.inspect(name)
            text = inspected.home or self._podman.run_in(name, self._user, ("/bin/sh", "-c", 'printf %s "$HOME"'))
        except (ContainerError, ValueError):
            _LOG.warning("cannot read HOME of container %s", name, exc_info=True)
            return None
        home = Path(text)
        if not home.is_absolute() or not home.is_dir():
            return None
        self._homes[name] = home
        return home

    def signed_in(self, kind: AgentKind) -> bool | None:
        """Whether the agent's CLI has a login in its container. None when the container's home cannot be read."""
        home = self.agent_home(f"agent-{kind.value}")
        if home is None:
            return None
        if kind is AgentKind.OPENCODE:
            return opencode_limits.signed_in(home)
        return (home / _LOGIN_FILES[kind]).is_file()

    def ensure_container(self, name: str) -> None:
        try:
            inspected = self._podman.inspect(name)
            if not inspected.running:
                self._podman.start(name)
        except (ContainerError, ValueError) as exc:
            raise TabRequestError(_START_FAILED) from exc

    def exec_argv(self, name: str, command: tuple[str, ...], env: dict[str, str], workdir: str) -> tuple[str, ...]:
        try:
            return self._podman.exec_spec(
                ExecRequest(name=name, command=(*LOGIN_SHELL, *command), env=env, workdir=workdir, user=self._user),
            )
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

    def set_worktrees_dir(self, path: Path) -> None:
        """Use `Settings.worktrees_dir`. A leading `~` is the user's home."""
        self._worktrees = path.expanduser()


@final
class HostRepos:
    """`RepoInspector` backed by git."""

    def __init__(self, git: Path = _GIT, *, timeout_s: float = _GIT_TIMEOUT_S) -> None:
        self._git = git
        self._timeout_s = timeout_s

    def toplevel(self, path: Path) -> tuple[Path, str] | None:
        top = self._ask(path, "rev-parse", "--show-toplevel")
        if top is None:
            return None
        # A repository without commits has a symbolic HEAD too, so this names the branch it would commit to.
        return Path(top), self._ask(path, "symbolic-ref", "--short", "HEAD") or _DEFAULT_BRANCH

    def _ask(self, path: Path, *args: str) -> str | None:
        try:
            done = subprocess.run(  # noqa: S603 -- argv is a list; the git path is absolute
                [str(self._git), "-C", str(path), *args],
                check=False, capture_output=True, text=True, timeout=self._timeout_s,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None


@final
class HostAgents:
    """The agents and their containers. Cached: the snapshot must not wait for podman each time."""

    def __init__(
        self,
        inspect: Callable[[str], Inspected],
        *,
        ttl_s: float = _AGENTS_TTL_S,
        signed_in: Callable[[AgentKind], bool | None] = lambda _kind: None,
    ) -> None:
        self._inspect = inspect
        self._signed_in = signed_in
        self._ttl_s = ttl_s
        self._lock = threading.Lock()
        self._until = 0.0
        self._cached: tuple[AgentInfo, ...] = ()

    def agents(self) -> tuple[AgentInfo, ...]:
        with self._lock:
            now = time.monotonic()
            if now >= self._until:
                self._cached = tuple(self._probe(kind) for kind in _AGENTS)
                self._until = now + self._ttl_s
            return self._cached

    def _probe(self, kind: AgentKind) -> AgentInfo:
        container = f"agent-{kind.value}"
        reason = None
        try:
            _ = self._inspect(container)
        except (ContainerError, ValueError, OSError):
            reason = f"container {container} not found"
        # A stopped container still counts: `tab/launchSpec` starts it.
        return AgentInfo(
            kind=kind, display_name=_DISPLAY.get(kind, kind.value.capitalize()), container=container,
            available=reason is None, unavailable_reason=reason,
            logged_in=self._signed_in(kind) if reason is None else None, version=None, supported=True,
            capabilities=CAPABILITIES[DomainAgent(kind.value)].names(),
        )


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
