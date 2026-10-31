"""Tab use cases for APP_API §4.3. The UI starts the process from `tab/launchSpec`."""

from __future__ import annotations

import hmac
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol, final

import msgspec

from aconsole.contracts import API_VERSION
from aconsole.contracts.app import (
    AppSnapshot,
    CloseTabParams,
    CloseTabResult,
    EmptyParams,
    EmptyResult,
    ExitedParams,
    FocusParams,
    OpenTabParams,
    RenameParams,
    ReorderParams,
    RestartParams,
    SettingsUpdateParams,
    SetupState,
    StartedParams,
    TabIdParams,
    TabView,
    TerminalLaunchSpec,
)
from aconsole.contracts.common import AgentKind, NodeId, ProjectId, TabId, TabStatus, WorkspaceMode
from aconsole.contracts.events import TabAdded, TabRemoved, TabUpdated, UiEvent
from aconsole.contracts.settings import Settings, commit_settings
from aconsole.core.application.ports.tab_store import StoredTab, TabDirectory, TabStore
from aconsole.core.domain.clock import Clock

_ZERO: Final = 0
_ONE: Final = 1
_STOP_S: Final = 3.0
_SCROLLBACK: Final = 10_000
_TITLE: Final = 80
_CLEAN: Final = "clean"
_KEEP: Final = "keep"
_REMOVE: Final = "remove"
_HEAD: Final = "HEAD"
_PATH_ENV: Final = "PATH"
_LOGIN: Final = "AGENT_NOT_LOGGED_IN"
_METHOD: Final = "METHOD_NOT_FOUND"
_MODIFIED: Final = "CONTENT_MODIFIED"
_PROJECT: Final = "PROJECT_NOT_FOUND"
_UNAVAILABLE: Final = "AGENT_UNAVAILABLE"
_SESSION: Final = "SESSION_NOT_FOUND"
_SCAN_FAILED: Final = "CONTAINER_START_FAILED"
_DIRTY: Final = "WORKTREE_DIRTY"
# Only ignored files (`.venv/`, included copies) differ from the base: removing needs the user's `remove`.
_IGNORED: Final = "ignored"
_CREATE: Final = "WORKTREE_CREATE_FAILED"
_MISSING_TAB: Final = "TAB_NOT_FOUND"
_ENV_TAB: Final = "ACONSOLE_TAB"
_ENV_INGEST: Final = "ACONSOLE_TAB_TOKEN"
_ENV_SIDECAR: Final = "ACONSOLE_SIDECAR_TOKEN"
_ENV_SOCKET: Final = "ACONSOLE_SOCKET"
_ENV_PORT: Final = "ACONSOLE_PORT"
_SIDECAR: Final = ("-I", "-m", "aconsole.sidecar", "--")
# APP_API §8: these statuses need the user.
_ATTENTION: Final = frozenset({TabStatus.WAITING_APPROVAL, TabStatus.WAITING_INPUT, TabStatus.ERROR})
_CODEX_ID: Final = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _launch_command(agent: AgentKind, session: str | None, *, fork: bool) -> tuple[str, ...]:
    """Claude flags, or Codex ``resume`` / ``fork``. Other agents stay a single argv word."""
    if agent is AgentKind.CLAUDE:
        return _claude_command(session, fork=fork)
    if agent is AgentKind.CODEX:
        return _codex_command(session, fork=fork)
    return (agent.value,)


def _claude_command(session: str | None, *, fork: bool) -> tuple[str, ...]:
    if session is None and not fork:
        return ("claude",)
    command = ["claude"]
    if session is not None:
        command.extend(("--resume", session))
    if fork:
        command.append("--fork-session")
    return tuple(command)


def _codex_command(session: str | None, *, fork: bool) -> tuple[str, ...]:
    if session is None or _CODEX_ID.fullmatch(session) is None:
        return ("codex",)
    if fork:
        return ("codex", "fork", session)
    return ("codex", "resume", session)


def _resting(status: TabStatus) -> TabStatus:
    """A dead process cannot still be working. Idle and exited are already resting states."""
    if status is TabStatus.IDLE or status is TabStatus.EXITED:
        return status
    return TabStatus.EXITED


# Hook event -> tab status. Claude and Codex share these names; unknown events leave the status alone.
_HOOK_STATUS: Final = {
    "SessionStart": TabStatus.IDLE,
    "UserPromptSubmit": TabStatus.WORKING,
    "PreToolUse": TabStatus.WORKING,
    "PostToolUse": TabStatus.WORKING,
    "PermissionRequest": TabStatus.WAITING_APPROVAL,
    "Notification": TabStatus.WAITING_INPUT,
    "Stop": TabStatus.IDLE,
    "SubagentStop": TabStatus.WORKING,
    "StopFailure": TabStatus.ERROR,
    "SessionEnd": TabStatus.IDLE,
    "agent-turn-complete": TabStatus.IDLE,
}

TAB_METHODS: Final = frozenset(
    {
        "tab/open",
        "tab/launchSpec",
        "tab/started",
        "tab/exited",
        "tab/close",
        "tab/restart",
        "tab/fork",
        "tab/rename",
        "tab/reorder",
        "tab/focus",
    }
)

SETTINGS_METHODS: Final = frozenset({"settings/get", "settings/update"})


@final
class TabRequestError(Exception):
    """A tab method refused the call. `name` is a contract error."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(name)


class TabPorts(Protocol):
    """Worktrees, containers, and process signals. The service does not spawn pods."""

    def new_id(self) -> str: ...

    def new_secret(self) -> str: ...

    def pause(self, seconds: float) -> None: ...

    def stop_process(self, pid: int) -> None: ...

    def create_worktree(self, repo: str, tab_id: str, slug: str, base: str) -> tuple[str, str]: ...

    def worktree_state(self, repo: str, dest: str) -> str: ...

    def release_worktree(self, dest: str) -> None: ...

    def remove_worktree(self, repo: str, dest: str, *, discard_ignored: bool) -> bool: ...

    def ensure_container(self, name: str) -> None: ...

    def exec_argv(self, name: str, command: tuple[str, ...], env: dict[str, str], workdir: str) -> tuple[str, ...]: ...

    def orphans(self, name: str, tab_id: str) -> tuple[int, ...]:
        """Pids still marked with the tab. Raises `TabRequestError` when the scan itself failed."""
        ...

    def container_path(self, name: str) -> str: ...

    def base_env(self) -> dict[str, str]: ...

    def python_bin(self) -> str: ...

    def socket_path(self) -> str: ...

    def ingest_port(self) -> str: ...


@dataclass(frozen=True, kw_only=True)
class ProjectSeed:
    """One project the tab book can open. Sessions are ids `tab/open` may resume."""

    project_id: str
    root: str
    container: str
    available: bool = True
    logged_in: bool = True
    sessions: frozenset[str] = frozenset()


@dataclass
class _Tab:
    id: str
    project_id: str
    agent: AgentKind
    title: str
    order: int
    status: TabStatus
    status_detail: str | None
    since: datetime
    attention: bool
    worktree: str
    branch: str | None
    base: str | None
    dirty: bool
    plan_node: str | None
    session: str | None
    fork: bool
    running: bool
    repo: str
    container: str
    created_here: bool
    created: datetime
    mode: str
    ingest: str
    sidecar: str
    pid: int | None


def _by_order(tab: _Tab) -> tuple[int, str]:
    return tab.order, tab.id


@final
class TabService:
    """In-memory tabs. One shared instance serves every IPC connection."""

    def __init__(
        self,
        clock: Clock,
        ports: TabPorts,
        *,
        stop_s: float = _STOP_S,
        fork_new_worktree: bool = False,
        listener: Callable[[UiEvent], None] | None = None,
    ) -> None:
        if stop_s < _ZERO:
            message = "stop timeout cannot be negative"
            raise ValueError(message)
        self._clock = clock
        self._ports = ports
        self._stop_s = stop_s
        self._fork_new = fork_new_worktree
        self._projects: dict[str, ProjectSeed] = {}
        self._tabs: dict[str, _Tab] = {}
        self._window_active = True
        self._focused: str | None = None
        self._store: TabStore | None = None
        self._settings_rev = 0
        self._approvals_mode: str = "inline"
        self._approval_hold_seconds: int = 540
        self._setup_completed = False
        self._failing_checks = 0
        self._warnings = 0
        # Receives tab_added / tab_updated / tab_removed on the thread that changed the tab.
        self.listener: Callable[[UiEvent], None] | None = listener
        # Filled when `tab/launchSpec` mints an ingest token for a restored tab. The daemon registers it.
        self.on_ingest: Callable[[str, str], None] | None = None
        self._routes: dict[str, Callable[[bytes], msgspec.Struct]] = {
            "tab/open": self._route_open,
            "tab/launchSpec": self._route_launch,
            "tab/started": self._route_started,
            "tab/exited": self._route_exited,
            "tab/close": self._route_close,
            "tab/restart": self._route_restart,
            "tab/fork": self._route_fork,
            "tab/rename": self._route_rename,
            "tab/reorder": self._route_reorder,
            "tab/focus": self._route_focus,
            "settings/get": self._route_settings_get,
            "settings/update": self._route_settings_update,
        }

    def add_project(self, seed: ProjectSeed) -> None:
        """Register a project. `project/add` is a later method; tests and the host pass the seed."""
        self._projects[seed.project_id] = seed

    def use_store(self, store: TabStore) -> None:
        """Persist later changes. Existing in-memory tabs are written by `reload` or the next edit."""
        self._store = store

    def note_settings(self, rev: int, *, setup_completed: bool = False) -> None:
        """Remember the settings revision and wizard flag the snapshot reports."""
        self._settings_rev = rev
        self._setup_completed = setup_completed

    def note_setup_checks(self, failing: int, warnings: int) -> None:
        """Record live setup checks. They never reopen the wizard."""
        self._failing_checks = failing
        self._warnings = warnings

    def reload(self) -> None:
        """Replace the book with the open rows. A process that died with the core is exited or idle.

        Each restored tab emits `tab_added`. Tokens are left empty until `tab/launchSpec`.
        """
        store = self._store
        if store is None:
            return
        for saved in store.load_open():
            self._adopt(saved)

    def snapshot(self) -> AppSnapshot:
        """The tabs the core would hand to `app/snapshot`, in display order."""
        ordered = sorted(self._tabs.values(), key=_by_order)
        stored = self._stored_settings()
        return AppSnapshot(
            api_version=API_VERSION,
            projects=(),
            active_project=None,
            agents=(),
            accounts=(),
            tabs=tuple(self._view(tab) for tab in ordered),
            approvals=(),
            plans=(),
            vault_ok=False,
            setup=SetupState(
                required=not stored.setup_completed,
                failing_checks=self._failing_checks,
                warnings=self._warnings,
            ),
            settings_rev=stored.rev,
        )

    def running_count(self) -> int:
        """Tabs whose process the UI reported started and not yet exited. The core stays up for them."""
        return sum(1 for tab in self._tabs.values() if tab.running)

    def ingest_token(self, tab_id: str) -> str | None:
        """The bearer token the tab's hooks present. Empty after a restore, until `tab/launchSpec`."""
        tab = self._tabs.get(tab_id)
        if tab is None or tab.ingest == "":
            return None
        return tab.ingest

    def claim_sidecar(self, tab_id: str, token: str) -> bool:
        """Accept the one-shot sidecar token of `tab/launchSpec` once. A second use, or any other token, fails."""
        tab = self._tabs.get(tab_id)
        if tab is None or tab.sidecar == "" or not hmac.compare_digest(tab.sidecar, token):
            return False
        tab.sidecar = ""
        return True

    def apply_hook(self, tab_id: str, event_name: str) -> TabView | None:
        """Move the tab to the status a hook event implies. None when the tab is gone or the event is not mapped."""
        tab = self._tabs.get(tab_id)
        status = _HOOK_STATUS.get(event_name)
        if tab is None or status is None:
            return None
        changed = {"status", "status_since"}
        tab.status = status
        tab.status_detail = None
        tab.since = self._clock.now()
        if self._refresh_attention(tab):
            changed.add("attention")
        self._remember(tab)
        return self._updated(tab, changed)

    def current_settings(self) -> Settings:
        """The settings document. Unset strings stay empty until a patch writes them."""
        found = self._stored_settings()
        worktrees = found.worktrees_dir if found.worktrees_dir is not None else ""
        return Settings(
            rev=found.rev,
            vault_path="",
            worktrees_dir=worktrees,
            setup_completed=found.setup_completed,
            approvals_mode="native" if found.approvals_mode == "native" else "inline",
            approval_hold_seconds=found.approval_hold_seconds,
        )

    def update_settings(self, params: SettingsUpdateParams) -> Settings:
        """Apply a settings patch. The same values do not grow `rev`."""
        current = self.current_settings()
        if params.rev != current.rev:
            raise TabRequestError(_MODIFIED)
        nxt = commit_settings(current, params.patch)
        if nxt == current:
            return current
        self._settings_rev = nxt.rev
        self._setup_completed = nxt.setup_completed
        self._approvals_mode = nxt.approvals_mode
        self._approval_hold_seconds = nxt.approval_hold_seconds
        store = self._store
        if store is not None:
            store.save_setup(completed=nxt.setup_completed, rev=nxt.rev)
            # Older TabStore implementations persist only setup; the database adapter also stores approval settings.
            save_approval = getattr(store, "save_approval_settings", None)
            if callable(save_approval):
                _ = save_approval(mode=nxt.approvals_mode, hold=nxt.approval_hold_seconds, rev=nxt.rev)
        return nxt

    def _stored_settings(self) -> TabDirectory:
        store = self._store
        if store is None:
            return TabDirectory(worktrees_dir=None, rev=self._settings_rev, setup_completed=self._setup_completed,
                approvals_mode=self._approvals_mode, approval_hold_seconds=self._approval_hold_seconds)
        return store.settings()

    def _route_settings_get(self, raw: bytes) -> Settings:
        _ = msgspec.json.decode(raw, type=EmptyParams)
        return self.current_settings()

    def _route_settings_update(self, raw: bytes) -> Settings:
        return self.update_settings(msgspec.json.decode(raw, type=SettingsUpdateParams))

    def dispatch(self, method: str, raw: bytes) -> msgspec.Struct:
        """Decode one §4.3 call and return its result struct."""
        route = self._routes.get(method)
        if route is None:
            raise TabRequestError(_METHOD)
        return route(raw)

    def open_tab(self, params: OpenTabParams) -> TabView:
        """Create the tab and, when asked, the worktree. The process stays stopped."""
        project = self._projects.get(str(params.project_id))
        if project is None:
            raise TabRequestError(_PROJECT)
        if not project.available:
            raise TabRequestError(_UNAVAILABLE)
        if params.resume_session is not None and params.resume_session not in project.sessions:
            raise TabRequestError(_SESSION)
        tab_id = self._ports.new_id()
        dest, branch, created = self._place(params, project, tab_id)
        detail = None if project.logged_in else _LOGIN
        now = self._clock.now()
        tab = _Tab(
            id=tab_id,
            project_id=project.project_id,
            agent=params.agent,
            title=self._title(params.title, params.agent, tab_id),
            order=self._next_order(),
            status=TabStatus.IDLE,
            status_detail=detail,
            since=now,
            attention=False,
            worktree=dest,
            branch=branch,
            base=params.base_branch,
            dirty=False,
            plan_node=None if params.plan_node is None else str(params.plan_node),
            session=params.resume_session,
            fork=params.fork,
            running=False,
            repo=project.root,
            container=project.container,
            created_here=created,
            created=now,
            mode=params.workspace.value,
            ingest=self._ports.new_secret(),
            sidecar="",
            pid=None,
        )
        self._tabs[tab_id] = tab
        self._remember(tab)
        return self._added(tab)

    def launch_spec(self, params: TabIdParams) -> TerminalLaunchSpec:
        """A new one-shot sidecar secret and the argv the UI spawns. This is the lazy start."""
        tab = self._require(str(params.tab_id))
        self._ports.ensure_container(tab.container)
        if tab.ingest == "":
            tab.ingest = self._ports.new_secret()
            notify = self.on_ingest
            if notify is not None:
                notify(tab.id, tab.ingest)
        secret = self._ports.new_secret()
        tab.sidecar = secret
        path = self._ports.container_path(tab.container)
        command = _launch_command(tab.agent, tab.session, fork=tab.fork)
        # Hooks run inside the container: they need the tab, its ingest token, and the ingest port (INGEST_HTTP §1).
        inner = {
            _ENV_TAB: tab.id,
            _ENV_INGEST: tab.ingest,
            _ENV_PORT: self._ports.ingest_port(),
            _PATH_ENV: path,
        }
        podman = self._ports.exec_argv(tab.container, command, inner, tab.worktree)
        env = dict(self._ports.base_env())
        env[_ENV_TAB] = tab.id
        env[_ENV_INGEST] = tab.ingest
        env[_ENV_SIDECAR] = secret
        env[_ENV_SOCKET] = self._ports.socket_path()
        env[_ENV_PORT] = self._ports.ingest_port()
        argv = (self._ports.python_bin(), *_SIDECAR, *podman)
        return TerminalLaunchSpec(argv=argv, env=env, cwd=tab.worktree, scrollback_lines=_SCROLLBACK, title=tab.title)

    def started(self, params: StartedParams) -> EmptyResult:
        tab = self._require(str(params.tab_id))
        tab.pid = params.pid
        tab.running = True
        tab.status = TabStatus.WORKING
        tab.since = self._clock.now()
        _ = self._refresh_attention(tab)
        self._remember(tab)
        _ = self._updated(tab, {"started", "status", "status_since", "attention"})
        return EmptyResult()

    def exited(self, params: ExitedParams) -> EmptyResult:
        tab = self._require(str(params.tab_id))
        tab.pid = None
        tab.running = False
        tab.status = TabStatus.EXITED
        tab.status_detail = str(params.wait_status)
        tab.since = self._clock.now()
        _ = self._refresh_attention(tab)
        self._remember(tab)
        _ = self._updated(tab, {"started", "status", "status_detail", "status_since", "attention"})
        return EmptyResult()

    def close_tab(self, params: CloseTabParams) -> CloseTabResult:
        """Dirty created worktree and no decision asks the UI. Otherwise stop, then maybe remove."""
        tab = self._require(str(params.tab_id))
        summary = self._summary(tab)
        if summary is not None and params.decision is None:
            tab.dirty = True
            self._remember(tab)
            return CloseTabResult(closed=False, needs_decision=True, dirty_summary=summary)
        if summary not in {None, _IGNORED} and params.decision == _REMOVE:
            raise TabRequestError(_DIRTY)
        stopped = self._stop(tab)
        # A failed orphan scan may leave processes in the tree: keep it rather than delete files under them.
        decision = params.decision if stopped else _KEEP
        self._finish_tree(tab, decision, discard_ignored=summary == _IGNORED)
        self._forget(tab, drop_worktree=tab.created_here and decision != _KEEP)
        del self._tabs[tab.id]
        if self._focused == tab.id:
            self._focused = None
        self._emit(TabRemoved(tab_id=TabId(tab.id)))
        return CloseTabResult(closed=True, needs_decision=False, dirty_summary=None)

    def restart(self, params: RestartParams) -> TabView:
        """Stop the old process and its orphans first, so a restart never leaves two agents in one tree.

        When the orphan scan fails, the known pid is stopped but the tab does not go idle: an orphan may still
        hold the tree, so the restart fails with CONTAINER_START_FAILED and the user can retry or close.
        """
        tab = self._require(str(params.tab_id))
        scanned = self._stop(tab)
        tab.pid = None
        tab.running = False
        if not scanned:
            _ = self._updated(tab, {"started"})
            raise TabRequestError(_SCAN_FAILED)
        tab.status = TabStatus.IDLE
        tab.since = self._clock.now()
        if not params.resume:
            tab.session = None
        _ = self._refresh_attention(tab)
        self._remember(tab)
        return self._updated(tab, {"started", "status", "status_since", "attention", "agent_session_id"})

    def fork(self, params: TabIdParams) -> TabView:
        tab = self._require(str(params.tab_id))
        tab_id = self._ports.new_id()
        dest = tab.worktree
        branch = tab.branch
        created = False
        mode = WorkspaceMode.EXISTING_WORKTREE.value
        if self._fork_new:
            slug = f"{tab.agent.value}-fork"
            dest, branch = self._ports.create_worktree(tab.repo, tab_id, slug, tab.base or _HEAD)
            created = True
            mode = WorkspaceMode.NEW_WORKTREE.value
        now = self._clock.now()
        copy = _Tab(
            id=tab_id,
            project_id=tab.project_id,
            agent=tab.agent,
            title=self._title(f"fork {tab.title}", tab.agent, tab_id),
            order=self._next_order(),
            status=TabStatus.IDLE,
            status_detail=None,
            since=now,
            attention=False,
            worktree=dest,
            branch=branch,
            base=tab.base,
            dirty=False,
            plan_node=tab.plan_node,
            session=tab.session,
            fork=tab.fork,
            running=False,
            repo=tab.repo,
            container=tab.container,
            created_here=created,
            created=now,
            mode=mode,
            ingest=self._ports.new_secret(),
            sidecar="",
            pid=None,
        )
        self._tabs[tab_id] = copy
        self._remember(copy)
        return self._added(copy)

    def rename(self, params: RenameParams) -> EmptyResult:
        tab = self._require(str(params.tab_id))
        tab.title = params.title
        self._remember(tab)
        _ = self._updated(tab, {"title"})
        return EmptyResult()

    def reorder(self, params: ReorderParams) -> EmptyResult:
        tab = self._require(str(params.tab_id))
        tab.order = params.order
        self._remember(tab)
        _ = self._updated(tab, {"order"})
        return EmptyResult()

    def focus(self, params: FocusParams) -> EmptyResult:
        """Record the focused tab and window state, then recompute `attention` by APP_API §8 for every tab."""
        if params.tab_id is not None:
            _ = self._require(str(params.tab_id))
        self._window_active = params.window_active
        self._focused = None if params.tab_id is None else str(params.tab_id)
        for tab in self._tabs.values():
            if self._refresh_attention(tab):
                _ = self._updated(tab, {"attention"})
        return EmptyResult()

    def _refresh_attention(self, tab: _Tab) -> bool:
        """Set `attention` from the status and focus. True when it changed."""
        seen = tab.id == self._focused and self._window_active
        attention = tab.status in _ATTENTION and not seen
        changed = attention != tab.attention
        tab.attention = attention
        return changed

    def _added(self, tab: _Tab) -> TabView:
        view = self._view(tab)
        self._emit(TabAdded(tab=view))
        return view

    def _updated(self, tab: _Tab, changed: set[str]) -> TabView:
        view = self._view(tab)
        self._emit(TabUpdated(tab=view, changed=frozenset(changed)))
        return view

    def _emit(self, event: UiEvent) -> None:
        listener = self.listener
        if listener is not None:
            listener(event)

    def _route_open(self, raw: bytes) -> TabView:
        return self.open_tab(msgspec.json.decode(raw, type=OpenTabParams))

    def _route_launch(self, raw: bytes) -> TerminalLaunchSpec:
        return self.launch_spec(msgspec.json.decode(raw, type=TabIdParams))

    def _route_started(self, raw: bytes) -> EmptyResult:
        return self.started(msgspec.json.decode(raw, type=StartedParams))

    def _route_exited(self, raw: bytes) -> EmptyResult:
        return self.exited(msgspec.json.decode(raw, type=ExitedParams))

    def _route_close(self, raw: bytes) -> CloseTabResult:
        return self.close_tab(msgspec.json.decode(raw, type=CloseTabParams))

    def _route_restart(self, raw: bytes) -> TabView:
        return self.restart(msgspec.json.decode(raw, type=RestartParams))

    def _route_fork(self, raw: bytes) -> TabView:
        return self.fork(msgspec.json.decode(raw, type=TabIdParams))

    def _route_rename(self, raw: bytes) -> EmptyResult:
        return self.rename(msgspec.json.decode(raw, type=RenameParams))

    def _route_reorder(self, raw: bytes) -> EmptyResult:
        return self.reorder(msgspec.json.decode(raw, type=ReorderParams))

    def _route_focus(self, raw: bytes) -> EmptyResult:
        return self.focus(msgspec.json.decode(raw, type=FocusParams))

    def _place(self, params: OpenTabParams, project: ProjectSeed, tab_id: str) -> tuple[str, str | None, bool]:
        if params.workspace == WorkspaceMode.MAIN_CHECKOUT:
            return project.root, params.base_branch, False
        if params.workspace == WorkspaceMode.EXISTING_WORKTREE:
            if params.existing_worktree is None:
                raise TabRequestError(_CREATE)
            return params.existing_worktree, params.base_branch, False
        slug = params.worktree_name or params.agent.value
        base = params.base_branch or _HEAD
        dest, branch = self._ports.create_worktree(project.root, tab_id, slug, base)
        return dest, branch, True

    def _summary(self, tab: _Tab) -> str | None:
        if not tab.created_here:
            return None
        state = self._ports.worktree_state(tab.repo, tab.worktree)
        if state == _CLEAN:
            return None
        return state

    def _finish_tree(self, tab: _Tab, decision: str | None, *, discard_ignored: bool) -> None:
        if not tab.created_here:
            return
        if decision == _KEEP:
            self._ports.release_worktree(tab.worktree)
            return
        _ = self._ports.remove_worktree(tab.repo, tab.worktree, discard_ignored=discard_ignored)

    def _stop(self, tab: _Tab) -> bool:
        """Stop the tab process and its orphans. False when the orphan scan failed."""
        if tab.pid is not None:
            self._ports.stop_process(tab.pid)
            self._ports.pause(self._stop_s)
        try:
            found = self._ports.orphans(tab.container, tab.id)
        except TabRequestError:
            return False
        for pid in found:
            self._ports.stop_process(pid)
        return True

    def _require(self, tab_id: str) -> _Tab:
        tab = self._tabs.get(tab_id)
        if tab is None:
            raise TabRequestError(_MISSING_TAB)
        return tab

    def _next_order(self) -> int:
        if not self._tabs:
            return _ZERO
        return max(tab.order for tab in self._tabs.values()) + _ONE

    def _title(self, requested: str | None, agent: AgentKind, tab_id: str) -> str:
        if requested:
            return requested[:_TITLE]
        return f"{agent.value}-{tab_id}"[:_TITLE]

    def _adopt(self, saved: StoredTab) -> None:
        status = _resting(TabStatus(saved.status))
        current = saved if status.value == saved.status else replace(saved, status=status.value)
        if current is not saved:
            self._store_save(current)
        tab = self._materialize(current)
        self._tabs[tab.id] = tab
        _ = self._added(tab)

    def _materialize(self, saved: StoredTab) -> _Tab:
        status = _resting(TabStatus(saved.status))
        return _Tab(
            id=saved.id,
            project_id=saved.project_id,
            agent=AgentKind(saved.agent),
            title=saved.title,
            order=saved.order,
            status=status,
            status_detail=saved.status_detail,
            since=saved.since,
            attention=False,
            worktree=saved.worktree,
            branch=saved.branch,
            base=saved.base_branch,
            dirty=saved.dirty,
            plan_node=saved.plan_node,
            session=saved.session,
            fork=False,
            running=False,
            repo=saved.project_root,
            container=saved.container,
            created_here=saved.workspace_mode == WorkspaceMode.NEW_WORKTREE,
            created=saved.created_at,
            mode=saved.workspace_mode,
            ingest="",
            sidecar="",
            pid=None,
        )

    def _remember(self, tab: _Tab) -> None:
        self._store_save(self._export(tab))

    def _forget(self, tab: _Tab, *, drop_worktree: bool) -> None:
        store = self._store
        if store is None:
            return
        store.remove(tab.id, closed_at=self._clock.now(), drop_worktree=drop_worktree)

    def _store_save(self, saved: StoredTab) -> None:
        store = self._store
        if store is not None:
            store.save(saved)

    def _export(self, tab: _Tab) -> StoredTab:
        name = Path(tab.repo).name or tab.project_id
        return StoredTab(
            id=tab.id,
            project_id=tab.project_id,
            project_name=name[:_TITLE],
            project_root=tab.repo,
            container=tab.container,
            agent=tab.agent.value,
            title=tab.title,
            order=tab.order,
            status=tab.status.value,
            status_detail=tab.status_detail,
            since=tab.since,
            created_at=tab.created,
            worktree=tab.worktree,
            branch=tab.branch,
            base_branch=tab.base,
            dirty=tab.dirty,
            workspace_mode=tab.mode,
            plan_node=tab.plan_node,
            session=tab.session,
        )

    def _view(self, tab: _Tab) -> TabView:
        return TabView(
            id=TabId(tab.id),
            project_id=ProjectId(tab.project_id),
            agent=tab.agent,
            account_id=None,
            title=tab.title,
            order=tab.order,
            status=tab.status,
            status_detail=tab.status_detail,
            status_since=tab.since,
            attention=tab.attention,
            last_message=None,
            worktree_path=tab.worktree,
            branch=tab.branch,
            base_branch=tab.base,
            worktree_dirty=tab.dirty,
            ahead=_ZERO,
            behind=_ZERO,
            plan_id=None,
            plan_node=None if tab.plan_node is None else NodeId(tab.plan_node),
            plan_node_title=None,
            context_used_pct=None,
            session_cost_usd=None,
            agent_session_id=tab.session,
            pending_approvals=0,
            checkpoints=0,
            started=tab.running,
        )
