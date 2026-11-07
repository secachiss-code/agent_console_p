"""Tab use cases for APP_API §4.3. The UI starts the process from `tab/launchSpec`."""

from __future__ import annotations

import contextlib
import hmac
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol, final

import msgspec

from aconsole.contracts import API_VERSION
from aconsole.contracts.app import (
    AccountView,
    AgentInfo,
    AppSnapshot,
    CloseTabParams,
    CloseTabResult,
    EmptyParams,
    EmptyResult,
    ExitedParams,
    FocusParams,
    OpenTabParams,
    PreparedImagePaste,
    PrepareImagePasteParams,
    ProjectAddParams,
    ProjectIdParams,
    ProjectView,
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
from aconsole.contracts.events import GitStateUpdated, ProjectsChanged, TabAdded, TabRemoved, TabUpdated, UiEvent
from aconsole.contracts.settings import Settings, commit_settings
from aconsole.contracts.work_sessions import (
    WorkSessionCreateParams,
    WorkSessionHandoffParams,
    WorkSessionListParams,
    WorkSessionListResult,
    WorkSessionParams,
    WorkSessionRenameParams,
    WorkSessionRestoreParams,
    WorkSessionRestorePreview,
    WorkSessionRestorePreviewParams,
    WorkSessionResultParams,
    WorkSessionView,
)
from aconsole.core.application.ports.tab_store import (
    PREFERENCE_KEYS,
    ProjectRecord,
    ProjectStore,
    StoredTab,
    TabDirectory,
    TabStore,
)
from aconsole.core.application.services.git_state import GitProject, GitTab
from aconsole.core.application.services.projects import ProjectCatalog
from aconsole.core.application.services.work_session_history import WorkSessionHistory
from aconsole.core.application.services.work_sessions import WorkSessionService, session_view
from aconsole.core.domain.agent import CAPABILITIES
from aconsole.core.domain.agent import AgentKind as DomainAgent
from aconsole.core.domain.clock import Clock
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.work_session import WorkSession

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
_NOT_A_REPO: Final = "PROJECT_NOT_A_GIT_REPO"
_PATH_DENIED: Final = "PATH_NOT_ALLOWED"
_TAB_CONFLICT: Final = "TAB_STATE_CONFLICT"
_UNAVAILABLE: Final = "AGENT_UNAVAILABLE"
_SESSION: Final = "SESSION_NOT_FOUND"
_SCAN_FAILED: Final = "CONTAINER_START_FAILED"
# A launch retries a failed orphan scan a few times, then refuses to start. Close and restart try once.
_SCAN_ATTEMPTS: Final = 3
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
_CODEX_ASKS_APPROVAL: Final = ("Approval requested", "Codex wants to edit")
_CODEX_ASKS_INPUT: Final = ("Plan mode prompt", "Question:")
# Cursor has no hook for this. Read from the screen when it goes quiet; a line must start with the word.
_CURSOR_ASKS_APPROVAL: Final = re.compile(
    r"\[y/n\]|\(y/n\)|Run command\?|^\W*(?:Allow|Approve)\b", re.IGNORECASE | re.MULTILINE,
)
_SCREEN_SUBMITTED: Final = "input_submitted"
_SCREEN_QUIET: Final = "screen_quiet"
_SCREEN_ACTIVE: Final = "screen_active"
_ENV_SCREEN: Final = "ACONSOLE_SCREEN_WATCH"
_ASK_SETTLE_S: Final = 2.0
_CODEX_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
# Changes under a cent are not worth a UI update: a statusline comes after every reply.
_COST_STEP_USD: Final = 0.01
_MCP_STEP_BYTES: Final = 4096


def _launch_command(agent: AgentKind, session: str | None, *, fork: bool) -> tuple[str, ...]:
    """Each agent's own way to resume. Fork is refused earlier for agents that cannot do it."""
    if session is not None and _CODEX_ID.fullmatch(session) is None:
        raise TabRequestError(_SESSION)
    if agent is AgentKind.CLAUDE:
        return _claude_command(session, fork=fork)
    if agent is AgentKind.CODEX:
        return _codex_command(session, fork=fork)
    if agent is AgentKind.OPENCODE:
        return ("opencode",) if session is None else ("opencode", "-s", session)
    if agent is AgentKind.CURSOR:
        return ("agent",) if session is None else ("agent", "--resume", session)
    return (agent.value,)


def _require_fork(agent: AgentKind) -> None:
    """Without this the copy would resume the same session, and two terminals would write to it."""
    kind = DomainAgent(agent.value)
    if kind in CAPABILITIES and not CAPABILITIES[kind].fork:
        raise TabRequestError(_UNAVAILABLE)


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
        return ("codex", "fork", "--", session)
    return ("codex", "resume", "--", session)


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
        "workSession/handoff",
        "workSession/history",
        "workSession/restorePreview",
        "workSession/restore",
        "workSession/result",
        "workSession/integratePreview",
        "workSession/integrate",
        "workSession/cleanupWorker",
        "workSession/create",
        "workSession/list",
        "workSession/get",
        "workSession/rename",
        "workSession/close",
        "workSession/resume",
        "tab/open",
        "tab/launchSpec",
        "terminal/prepareImagePaste",
        "tab/started",
        "tab/exited",
        "tab/close",
        "tab/restart",
        "tab/fork",
        "tab/rename",
        "tab/reorder",
        "tab/focus",
        "project/add",
        "project/remove",
        "project/setActive",
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

    def prepare_image(self, worktree: str, container: str, png: bytes) -> str: ...

    def new_id(self) -> str: ...

    def new_secret(self) -> str: ...

    def pause(self, seconds: float) -> None: ...

    def stop_process(self, pid: int) -> None: ...

    def stop_in_container(self, name: str, pid: int) -> None: ...

    def create_worktree(self, repo: str, tab_id: str, slug: str, base: str) -> tuple[str, str]: ...

    def create_isolated_worktree(self, repo: str, tab_id: str, session_id: str, base: str) -> str: ...

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


class AgentCommands(Protocol):
    """The argv that wires an agent to this core. Reaching for files keeps it out of the service."""

    def command(
        self, agent: AgentKind, tab_id: str, token: str, container: str, port: str,
    ) -> tuple[str, ...] | None: ...

    def release(self, agent: AgentKind, tab_id: str, container: str) -> None: ...

    def session_exists(self, agent: AgentKind, session: str, container: str) -> bool: ...


@dataclass(frozen=True, kw_only=True)
class ProjectSeed:
    """One project the tab book can open. Sessions are ids `tab/open` may resume.

    An empty `container` means the agent's own one, `agent-<kind>`.
    """

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
    work_session_id: str | None = None
    isolated: bool = False
    needs_process_check: bool = False
    live_sidecar: str = ""
    # What the session has cost at API prices, as the agent reports it. Not kept: the agent says it again.
    cost_usd: float | None = None
    # What AConsole's MCP tools sent the agent this run, and how much of it the UI has heard of.
    mcp_bytes: int = 0
    mcp_calls: int = 0
    mcp_told: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewTarget:
    """What checkpoint and diff calls read from one tab."""

    worktree: str
    status: TabStatus
    base_branch: str | None
    started: bool


def _preferences_of(settings: Settings) -> dict[str, object]:
    """The keys the store keeps as they are, in JSON types."""
    plain: dict[str, object] = msgspec.to_builtins(settings)  # pyright: ignore[reportAny] -- a struct gives a dict
    return {key: plain[key] for key in PREFERENCE_KEYS}


def _with_preferences(base: Settings, stored: dict[str, object]) -> Settings:
    """A stored document that no longer fits the contract falls back to the defaults, and the app still opens."""
    plain: dict[str, object] = msgspec.to_builtins(base)  # pyright: ignore[reportAny] -- a struct gives a dict
    try:
        return msgspec.convert({**plain, **stored}, Settings)
    except msgspec.ValidationError:
        return base


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
        self.work_sessions = WorkSessionService(clock.now)
        self.history: WorkSessionHistory | None = None
        self.workspace_lock: Callable[[str], threading.RLock] = lambda _path: threading.RLock()
        self.inspect_workspace: Callable[[str, str], tuple[str, str | None]] | None = None
        self.session_git: Callable[[str, bytes], msgspec.Struct] | None = None
        self._stop_s = stop_s
        self._fork_new = fork_new_worktree
        self._projects: dict[str, ProjectSeed] = {}
        self._tabs: dict[str, _Tab] = {}
        self._window_active = True
        self._focused: str | None = None
        self._store: TabStore | None = None
        self._settings_rev = 0
        self._worktrees_dir: str | None = None
        self.on_settings_changed: Callable[[Settings], None] | None = None
        self._approvals_mode: str = "inline"
        self._approval_hold_seconds: int = 540
        self._setup_completed = False
        self._preferences: dict[str, object] = {}
        self._failing_checks = 0
        self._warnings = 0
        # Receives tab_added / tab_updated / tab_removed on the thread that changed the tab.
        self.listener: Callable[[UiEvent], None] | None = listener
        # Filled when `tab/launchSpec` mints an ingest token for a restored tab. The daemon registers it.
        self.on_ingest: Callable[[str, str], None] | None = None
        self._accounts: Callable[[], tuple[AccountView, ...]] | None = None
        self._catalog: ProjectCatalog | None = None
        self._agents: Callable[[], tuple[AgentInfo, ...]] | None = None
        self._commands: AgentCommands | None = None
        self._routes: dict[str, Callable[[bytes], msgspec.Struct]] = {
            "tab/open": self._route_open,
            "tab/launchSpec": self._route_launch,
            "terminal/prepareImagePaste": self._route_image_paste,
            "tab/started": self._route_started,
            "tab/exited": self._route_exited,
            "tab/close": self._route_close,
            "tab/restart": self._route_restart,
            "tab/fork": self._route_fork,
            "tab/rename": self._route_rename,
            "tab/reorder": self._route_reorder,
            "tab/focus": self._route_focus,
            "project/add": self._route_project_add,
            "project/remove": self._route_project_remove,
            "project/setActive": self._route_project_active,
            "settings/get": self._route_settings_get,
            "settings/update": self._route_settings_update,
        }

    def add_project(self, seed: ProjectSeed) -> None:
        """Register a project. `project/add` is a later method; tests and the host pass the seed."""
        self._projects[seed.project_id] = seed

    def use_projects(self, catalog: ProjectCatalog, store: ProjectStore | None = None) -> None:
        """Serve `project/*`. Projects saved in `store` can open tabs at once."""
        self._catalog = catalog
        if store is not None:
            for record in catalog.attach(store):
                self._seed(record)

    def use_agents(self, source: Callable[[], tuple[AgentInfo, ...]]) -> None:
        """The agent list for the snapshot. A kind it marks unavailable cannot open a tab."""
        self._agents = source

    def use_accounts(self, source: Callable[[], tuple[AccountView, ...]]) -> None:
        """The accounts and limits for the snapshot."""
        self._accounts = source

    def use_commands(self, commands: AgentCommands) -> None:
        self._commands = commands

    def _seed(self, record: ProjectRecord) -> None:
        self.add_project(ProjectSeed(project_id=record.id, root=record.root, container=""))

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

    def snapshot(self, *, include_runtime_details: bool = True) -> AppSnapshot:
        """The tabs the core would hand to `app/snapshot`, in display order."""
        ordered = sorted(self._tabs.values(), key=_by_order)
        stored = self._stored_settings()
        catalog = self._catalog
        return AppSnapshot(
            api_version=API_VERSION,
            projects=() if catalog is None else catalog.views(),
            active_project=None if catalog is None else catalog.active,
            agents=self._agents() if include_runtime_details and self._agents is not None else (),
            accounts=self._accounts() if include_runtime_details and self._accounts is not None else (),
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

    def busy_tabs(self) -> tuple[TabId, ...]:
        """Tabs whose process is still running, for the quit confirmation."""
        return tuple(TabId(tab.id) for tab in sorted(self._tabs.values(), key=_by_order) if tab.running)

    def stop_agents(self) -> None:
        """Soft stop for every running tab. Tabs and sessions stay; the UI reports `tab/exited` as each one ends."""
        for tab in tuple(self._tabs.values()):
            if tab.running:
                _ = self._stop(tab)

    def ingest_token(self, tab_id: str) -> str | None:
        """The bearer token the tab's hooks present. Empty after a restore, until `tab/launchSpec`."""
        tab = self._tabs.get(tab_id)
        if tab is None or tab.ingest == "":
            return None
        return tab.ingest

    def codex_turn(self, cwd: str, *, working: bool) -> None:
        """A Codex session in ``cwd`` started or ended a turn. Moves the running Codex tabs of that directory."""
        event = "UserPromptSubmit" if working else "agent-turn-complete"
        wanted = TabStatus.WORKING if working else TabStatus.IDLE
        now = self._clock.now()
        for tab in tuple(self._tabs.values()):
            if tab.agent is not AgentKind.CODEX or not tab.running or tab.worktree != cwd or tab.status is wanted:
                continue
            # The lines Codex writes right after asking must not cancel the wait they belong to.
            just_asked = working and tab.status in _ATTENTION and (now - tab.since).total_seconds() < _ASK_SETTLE_S
            if not just_asked:
                _ = self.apply_hook(tab.id, event)

    def codex_session(self, cwd: str, session: str) -> None:
        """Remember which Codex session runs in ``cwd``. Two Codex tabs there cannot be told apart: neither is set."""
        running = [
            tab for tab in self._tabs.values() if tab.agent is AgentKind.CODEX and tab.running and tab.worktree == cwd
        ]
        if len(running) == 1:
            self._note_session(running[0], session)

    def reported_session(self, tab_id: str, session: str) -> None:
        """What a Claude hook or OpenCode's stream says the session is. A session replaced by `/clear` replaces this."""
        tab = self._tabs.get(tab_id)
        if tab is not None and tab.agent in {AgentKind.CLAUDE, AgentKind.OPENCODE}:
            self._note_session(tab, session)

    def _note_session(self, tab: _Tab, session: str) -> None:
        # It becomes a word of the command line that resumes the agent.
        if tab.session == session or _CODEX_ID.fullmatch(session) is None:
            return
        tab.session = session
        self._remember(tab)
        _ = self._updated(tab, {"agent_session_id"})

    def session_cost(self, tab_id: str, usd: float) -> None:
        """The agent's own running total for this tab's session. A cent's change or more is worth telling the UI."""
        tab = self._tabs.get(tab_id)
        if tab is None or usd < 0:
            return
        if tab.cost_usd is not None and abs(tab.cost_usd - usd) < _COST_STEP_USD:
            return
        tab.cost_usd = round(usd, 4)
        _ = self._updated(tab, {"session_cost_usd"})

    def mcp_sent(self, tab_id: str, size: int) -> None:
        """One MCP answer of ``size`` bytes went to this tab's agent. The UI hears of the first, then of every 4 KB."""
        tab = self._tabs.get(tab_id)
        if tab is None or size < 0:
            return
        tab.mcp_bytes += size
        tab.mcp_calls += 1
        if tab.mcp_told and tab.mcp_bytes - tab.mcp_told < _MCP_STEP_BYTES:
            return
        tab.mcp_told = tab.mcp_bytes
        _ = self._updated(tab, {"mcp_bytes", "mcp_calls"})

    def agent_notice(self, tab_id: str, text: str) -> None:
        """What Codex tells the terminal (OSC 9). Without hooks only these words say that it waits for the user."""
        tab = self._tabs.get(tab_id)
        if tab is None or tab.agent is not AgentKind.CODEX:
            return
        if text.startswith(_CODEX_ASKS_APPROVAL):
            _ = self.apply_hook(tab_id, "PermissionRequest")
        elif text.startswith(_CODEX_ASKS_INPUT):
            _ = self.apply_hook(tab_id, "Notification")

    def screen_signal(self, tab_id: str, kind: str, text: str | None) -> None:
        """The sidecar's view of a Cursor tab. Silence after work is the end of a turn, unless the screen asks."""
        tab = self._tabs.get(tab_id)
        if tab is None or tab.agent is not AgentKind.CURSOR or not tab.running:
            return
        if kind == _SCREEN_SUBMITTED:
            _ = self.apply_hook(tab_id, "UserPromptSubmit")
        elif kind == _SCREEN_ACTIVE:
            if tab.status is TabStatus.WAITING_APPROVAL:
                _ = self.apply_hook(tab_id, "PreToolUse")
        elif kind == _SCREEN_QUIET:
            if _CURSOR_ASKS_APPROVAL.search(text or "") is not None:
                if tab.status is not TabStatus.WAITING_APPROVAL:
                    _ = self.apply_hook(tab_id, "PermissionRequest")
            elif tab.status in {TabStatus.WORKING, TabStatus.WAITING_APPROVAL}:
                _ = self.apply_hook(tab_id, "agent-turn-complete")

    def claim_sidecar(self, tab_id: str, token: str) -> bool:
        """Accept the one-shot sidecar token of `tab/launchSpec` once. A second use, or any other token, fails."""
        tab = self._tabs.get(tab_id)
        if tab is None or tab.sidecar == "" or not hmac.compare_digest(tab.sidecar, token):
            return False
        tab.sidecar = ""
        tab.live_sidecar = token
        return True

    def sidecar_gone(self, tab_id: str, token: str) -> None:
        """The terminal of the tab is gone. `podman exec` leaves its agent running, and nothing can reach it again."""
        tab = self._tabs.get(tab_id)
        if tab is None or tab.live_sidecar != token:
            return
        tab.live_sidecar = ""
        _ = self._stop_orphans(tab)
        if not tab.running:
            return
        tab.pid = None
        tab.running = False
        tab.status = TabStatus.EXITED
        tab.since = self._clock.now()
        _ = self._refresh_attention(tab)
        self._remember(tab)
        _ = self._updated(tab, {"started", "status", "status_since", "attention"})

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

    def review_target(self, tab_id: str) -> ReviewTarget | None:
        """Where a tab's checkpoints and diff live. None for a tab this core does not hold."""
        tab = self._tabs.get(tab_id)
        if tab is None:
            return None
        return ReviewTarget(worktree=tab.worktree, status=tab.status, base_branch=tab.base, started=tab.running)

    def git_project(self, project_id: str) -> GitProject | None:
        """Any thread: a read of what this book holds."""
        seed = self._projects.get(project_id)
        if seed is None:
            return None
        catalog = self._catalog
        views = () if catalog is None else catalog.views()
        branch = next((view.default_branch for view in views if str(view.id) == project_id), None)
        return GitProject(project_id=project_id, root=seed.root, default_branch=branch)

    def git_tabs(self, project_id: str) -> tuple[GitTab, ...]:
        """Any thread. `list` copies the book at once, so a tab opening meanwhile cannot break this."""
        return tuple(
            GitTab(
                tab_id=tab.id,
                title=tab.title,
                agent=tab.agent,
                status=tab.status,
                worktree=tab.worktree,
                branch=tab.branch,
                plan_id=None,
                plan_node=tab.plan_node,
                work_session_id=tab.work_session_id,
            )
            for tab in list(self._tabs.values())
            if tab.project_id == project_id
        )

    def tab_ids(self) -> tuple[str, ...]:
        return tuple(self._tabs)

    def current_settings(self) -> Settings:
        """The settings document. Unset strings stay empty until a patch writes them."""
        found = self._stored_settings()
        worktrees = found.worktrees_dir if found.worktrees_dir is not None else ""
        base = Settings(
            rev=found.rev,
            vault_path="",
            worktrees_dir=worktrees,
            setup_completed=found.setup_completed,
            approvals_mode="native" if found.approvals_mode == "native" else "inline",
            approval_hold_seconds=found.approval_hold_seconds,
        )
        return _with_preferences(base, found.preferences)

    def update_settings(self, params: SettingsUpdateParams) -> Settings:
        """Apply a settings patch. The same values do not grow `rev`."""
        current = self.current_settings()
        if params.rev != current.rev:
            raise TabRequestError(_MODIFIED)
        nxt = commit_settings(current, params.patch)
        if nxt == current:
            return current
        preferences = _preferences_of(nxt)
        store = self._store
        if store is not None:
            store.save_settings(TabDirectory(
                worktrees_dir=nxt.worktrees_dir or None, rev=nxt.rev,
                setup_completed=nxt.setup_completed, approvals_mode=nxt.approvals_mode,
                approval_hold_seconds=nxt.approval_hold_seconds, preferences=preferences,
            ))
        self._settings_rev = nxt.rev
        self._worktrees_dir = nxt.worktrees_dir or None
        self._setup_completed = nxt.setup_completed
        self._approvals_mode = nxt.approvals_mode
        self._approval_hold_seconds = nxt.approval_hold_seconds
        self._preferences = preferences
        if self.on_settings_changed is not None:
            self.on_settings_changed(nxt)
        return nxt

    def _stored_settings(self) -> TabDirectory:
        store = self._store
        if store is None:
            return TabDirectory(
                worktrees_dir=self._worktrees_dir, rev=self._settings_rev, setup_completed=self._setup_completed,
                approvals_mode=self._approvals_mode, approval_hold_seconds=self._approval_hold_seconds,
                preferences=self._preferences)
        return store.settings()

    def _route_settings_get(self, raw: bytes) -> Settings:
        _ = msgspec.json.decode(raw, type=EmptyParams)
        return self.current_settings()

    def _route_settings_update(self, raw: bytes) -> Settings:
        return self.update_settings(msgspec.json.decode(raw, type=SettingsUpdateParams))

    def dispatch(self, method: str, raw: bytes) -> msgspec.Struct:
        """Decode one §4.3 call and return its result struct."""
        if method.startswith("workSession/"):
            result = self._session_dispatch(method, raw)
            if method not in {"workSession/list", "workSession/get", "workSession/history"} and "Preview" not in method:
                project = msgspec.json.decode(raw, type=ProjectIdParams)
                self._emit(GitStateUpdated(
                    project_id=project.project_id, revision=f"session:{self._clock.now().isoformat()}",
                ))
            return result
        route = self._routes.get(method)
        if route is None:
            raise TabRequestError(_METHOD)
        return route(raw)

    def _session_dispatch(  # noqa: C901, PLR0911, PLR0912 -- one branch per session RPC
        self, method: str, raw: bytes,
    ) -> msgspec.Struct:
        if method == "workSession/result":
            result = msgspec.json.decode(raw, type=WorkSessionResultParams)
            tab = self._require(str(result.tab_id))
            _ = self.work_sessions.require(str(result.project_id), result.work_session_id, active=True)
            if tab.work_session_id != result.work_session_id or not tab.isolated or self.history is None:
                message = "an isolated session member is required"
                raise ValueError(message)
            self._finish_session_actor(tab, "end")
            record = self.work_sessions.require(tab.project_id, result.work_session_id, active=True)
            _ = self.history.capture(record, tab.id, "result", f"Result: {tab.title}", worktree=tab.worktree)
            return self.history.view(record.id)
        if method == "workSession/handoff":
            return self.handoff(msgspec.json.decode(raw, type=WorkSessionHandoffParams))
        if method in {"workSession/restorePreview", "workSession/restore"}:
            return self._session_restore(method, raw)
        if method == "workSession/create":
            return self.create_work_session(msgspec.json.decode(raw, type=WorkSessionCreateParams))
        if method == "workSession/list":
            query = msgspec.json.decode(raw, type=WorkSessionListParams)
            if str(query.project_id) not in self._projects:
                raise TabRequestError(_PROJECT)
            return WorkSessionListResult(sessions=tuple(
                session_view(row) for row in self.work_sessions.all(
                    str(query.project_id), include_archived=query.include_archived,
                )
            ))
        if method == "workSession/rename":
            rename = msgspec.json.decode(raw, type=WorkSessionRenameParams)
            renamed = self.work_sessions.rename(str(rename.project_id), rename.work_session_id, rename.title)
            for tab in self._tabs.values():
                if tab.work_session_id == renamed.id:
                    _ = self._updated(tab, {"work_session_title"})
            return session_view(renamed)
        query = msgspec.json.decode(raw, type=WorkSessionParams)
        record = self.work_sessions.require(str(query.project_id), query.work_session_id)
        if method == "workSession/get":
            return session_view(record)
        if method == "workSession/history" and self.history is not None:
            return self.history.view(record.id)
        if method == "workSession/resume":
            if self.inspect_workspace is not None:
                _ = self.inspect_workspace(record.project_id, record.worktree)
            return session_view(self.work_sessions.resume(record.project_id, record.id))
        if method == "workSession/close":
            members = [tab for tab in self._tabs.values() if tab.work_session_id == record.id]
            for tab in members:
                self._finish_session_actor(tab, "archive")
            archived = self.work_sessions.archive(record.project_id, record.id)
            for tab in members:
                tab.pid = None
                tab.running = False
                tab.status = TabStatus.EXITED
                self._remember(tab)
                _ = self._updated(tab, {"started", "status"})
            return session_view(archived)
        if self.session_git is not None:
            return self.session_git(method, raw)
        raise TabRequestError(_METHOD)

    def handoff(self, params: WorkSessionHandoffParams) -> TabView:
        record = self.work_sessions.require(str(params.project_id), params.work_session_id, active=True)
        target = self._require(str(params.to_tab))
        if target.work_session_id != record.id:
            raise DomainError(code=3040, name="WORK_SESSION_NOT_FOUND", retryable=False)
        with self.workspace_lock(target.worktree):
            members = tuple(tab for tab in self._tabs.values() if tab.work_session_id == record.id
                            and Path(tab.worktree).resolve() == Path(target.worktree).resolve())
            active = tuple(tab for tab in members if tab.running or tab.sidecar or tab.live_sidecar)
            pending = None
            if self.history is not None:
                self.history.recover(record)
                pending = self.history.prepare(
                    record, active[0].id if len(active) == 1 else None,
                    "handoff", f"Hand off to {target.title}", worktree=target.worktree,
                )
            for tab in members:
                self._finish_session_actor(tab, "handoff", keep_snapshot=False)
            if self.history is not None and pending is not None:
                _ = self.history.complete(pending)
        return self._view(target)

    def _finish_session_actor(self, tab: _Tab, reason: str, *, keep_snapshot: bool = True) -> None:
        with self.workspace_lock(tab.worktree):
            had_process = bool(tab.running or tab.sidecar or tab.live_sidecar)
            needed_check = tab.needs_process_check
            tab.sidecar = ""
            tab.live_sidecar = ""
            if (had_process or tab.needs_process_check) and not self._stop(tab):
                tab.pid = None
                tab.running = False
                tab.needs_process_check = True
                tab.status = TabStatus.UNKNOWN
                self._remember(tab)
                _ = self._updated(tab, {"started", "status"})
                raise TabRequestError(_SCAN_FAILED)
            tab.needs_process_check = False
            tab.pid = None
            tab.running = False
            keep_result = tab.isolated and reason in {"close-tab", "archive"}
            if keep_snapshot and (had_process or keep_result or needed_check) and (
                self.history is not None and tab.work_session_id is not None
            ):
                record = self.work_sessions.require(tab.project_id, tab.work_session_id)
                kind = "result" if keep_result else reason
                actor = None if reason == "recovery" else tab.id
                _ = self.history.capture(record, actor, kind, f"{kind}: {tab.title}", worktree=tab.worktree)
            tab.status = TabStatus.EXITED
            tab.since = self._clock.now()
            self._remember(tab)
            _ = self._updated(tab, {"started", "status", "status_since"})

    def _session_restore(self, method: str, raw: bytes) -> msgspec.Struct:
        preview = msgspec.json.decode(raw, type=WorkSessionRestorePreviewParams) if method.endswith("Preview") else None
        restore = msgspec.json.decode(raw, type=WorkSessionRestoreParams) if preview is None else None
        query = preview if preview is not None else restore
        if query is None or self.history is None:
            raise TabRequestError(_METHOD)
        record = self.work_sessions.require(str(query.project_id), query.work_session_id, active=True)
        snapshot = self.history.require(record.id, query.sequence)
        if Path(snapshot.worktree).resolve() != Path(record.worktree).resolve():
            message = "integrate an isolated result instead of restoring it over the primary workspace"
            raise ValueError(message)
        if snapshot.oid is None:
            missing = "CHECKPOINT_NOT_FOUND"
            raise TabRequestError(missing)
        key = (record.id, query.sequence, snapshot.oid)
        if preview is not None:
            return WorkSessionRestorePreview(
                sequence=query.sequence, oid=snapshot.oid, confirm_token=self.history.tokens.issue(key),
            )
        if restore is None:
            raise TabRequestError(_METHOD)
        with self.workspace_lock(record.worktree):
            self.history.tokens.spend(key, restore.confirm_token)
            for tab in list(self._tabs.values()):
                if tab.work_session_id == record.id:
                    self._finish_session_actor(tab, "before-restore")
            _ = self.history.capture(record, None, "safety", "Before restoring session files")
            self.history.materialize(record, query.sequence)
        return self.history.view(record.id)

    def create_work_session(self, params: WorkSessionCreateParams) -> WorkSessionView:
        project = self._projects.get(str(params.project_id))
        if project is None:
            raise TabRequestError(_PROJECT)
        title = params.title.strip()
        if not title:
            message = "session title is empty"
            raise ValueError(message)
        session_id = self._ports.new_id()
        base = params.base_branch or self._project_base(project.project_id)
        branch = base
        if params.workspace is WorkspaceMode.NEW_WORKTREE:
            slug = re.sub(r"[^A-Za-z0-9_-]+", "-", title).strip("-_")[:30] or "task"
            dest, branch = self._ports.create_worktree(project.root, session_id, f"s-{slug}", base or _HEAD)
        elif params.workspace is WorkspaceMode.MAIN_CHECKOUT:
            dest = project.root
        elif params.existing_worktree is not None:
            dest = params.existing_worktree
        else:
            raise TabRequestError(_CREATE)
        if self.inspect_workspace is not None:
            dest, branch = self.inspect_workspace(project.project_id, dest)
        now = self._clock.now()
        record = self.work_sessions.add(WorkSession(
            id=session_id, project_id=project.project_id, title=title, worktree=dest,
            branch=branch, base_branch=base, workspace_mode=params.workspace.value,
            status="active", created_at=now, updated_at=now,
        ))
        return session_view(record)

    def _project_base(self, project_id: str) -> str | None:
        project = self.git_project(project_id)
        return None if project is None else project.default_branch

    def actor_context(self, tab_id: str) -> tuple[str, str, str | None]:
        tab = self._require(tab_id)
        return tab.title, tab.agent.value, tab.plan_node

    def archive_removed_workspace(self, project_id: str, path: str) -> None:
        record = self.work_sessions.at(project_id, path)
        if record is not None:
            _ = self.work_sessions.removed(project_id, record.id)

    def session_checkpoint(self, tab_id: str, label: str, kind: str, *, lock_held: bool = False) -> bool:
        tab = self._require(tab_id)
        if tab.work_session_id is None or self.history is None:
            return False
        record = self.work_sessions.require(tab.project_id, tab.work_session_id, active=True)
        with contextlib.nullcontext() if lock_held else self.workspace_lock(tab.worktree):
            point = self.history.capture(record, tab.id, kind, label, worktree=tab.worktree)
        self._emit(GitStateUpdated(project_id=ProjectId(tab.project_id), revision=point.ref))
        return True

    def close_for_cleanup(self, tab_id: str) -> None:
        """The Git caller holds this workspace's lock. Do not reacquire it on the tab worker."""
        tab = self._require(tab_id)
        if tab.running or tab.sidecar or tab.live_sidecar or not self._stop_orphans(tab):
            raise TabRequestError(_SCAN_FAILED)
        if self._commands is not None:
            self._commands.release(tab.agent, tab.id, tab.container)
        self._forget(tab, drop_worktree=False)
        del self._tabs[tab.id]
        if self._focused == tab.id:
            self._focused = None
        self._emit(TabRemoved(tab_id=TabId(tab.id)))

    def require_session_writable(self, tab_id: str) -> None:
        tab = self._require(tab_id)
        if tab.work_session_id is not None:
            _ = self.work_sessions.require(tab.project_id, tab.work_session_id, active=True)
        for member in self._tabs.values():
            same_path = Path(member.worktree).resolve() == Path(tab.worktree).resolve()
            if member.id != tab.id and same_path and member.status in {
                TabStatus.STARTING, TabStatus.WORKING, TabStatus.WAITING_APPROVAL, TabStatus.UNKNOWN,
            }:
                raise DomainError(code=3006, name="CHECKPOINT_RESTORE_CONFLICT", retryable=False)

    def open_tab(self, params: OpenTabParams) -> TabView:  # noqa: C901 -- new, shared and isolated workspaces
        """Create the tab and, when asked, the worktree. The process stays stopped."""
        project = self._projects.get(str(params.project_id))
        if project is None:
            raise TabRequestError(_PROJECT)
        if not project.available or (not project.container and self._agent_missing(params.agent)):
            raise TabRequestError(_UNAVAILABLE)
        if params.resume_session is not None and params.resume_session not in project.sessions:
            raise TabRequestError(_SESSION)
        if params.fork:
            _require_fork(params.agent)
        tab_id = self._ports.new_id()
        if params.isolated and params.work_session_id is None:
            message = "an isolated executor needs a work session"
            raise ValueError(message)
        session = None if params.work_session_id is None else self.work_sessions.require(
            project.project_id, params.work_session_id, active=True,
        )
        if session is None:
            dest, branch, created = self._place(params, project, tab_id)
            if self.inspect_workspace is not None:
                dest, branch = self.inspect_workspace(project.project_id, dest)
            session = self.work_sessions.at(project.project_id, dest)
        else:
            dest, branch, created = session.worktree, session.branch, False
            if params.isolated:
                dest = self._ports.create_isolated_worktree(project.root, tab_id, session.id, session.worktree)
                branch = None
                created = True
        if session is None:
            now = self._clock.now()
            session = WorkSession(
                id=f"s-{tab_id}", project_id=project.project_id,
                title=self._title(params.title, params.agent, tab_id), worktree=dest,
                branch=branch, base_branch=params.base_branch or self._project_base(project.project_id),
                workspace_mode=params.workspace.value, status="active", created_at=now, updated_at=now,
            )
        elif session.status != "active":
            raise DomainError(code=3041, name="WORK_SESSION_ARCHIVED", retryable=False)
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
            base=session.base_branch,
            dirty=False,
            plan_node=None if params.plan_node is None else str(params.plan_node),
            session=params.resume_session,
            fork=params.fork,
            running=False,
            repo=project.root,
            container=project.container or f"agent-{params.agent.value}",
            created_here=created,
            created=now,
            mode=WorkspaceMode.NEW_WORKTREE.value if params.isolated else session.workspace_mode,
            ingest=self._ports.new_secret(),
            sidecar="",
            pid=None,
            work_session_id=session.id,
            isolated=params.isolated,
        )
        self._tabs[tab_id] = tab
        try:
            self._remember(tab)
            _ = self.work_sessions.add(session)
        except Exception:
            del self._tabs[tab_id]
            raise
        return self._added(tab)

    def launch_spec(self, params: TabIdParams) -> TerminalLaunchSpec:
        """A new one-shot sidecar secret and the argv the UI spawns. This is the lazy start."""
        tab = self._require(str(params.tab_id))
        with self.workspace_lock(tab.worktree):
            return self._launch_spec(tab)

    def _launch_spec(self, tab: _Tab) -> TerminalLaunchSpec:
        self._require_session_launch(tab)
        self._ports.ensure_container(tab.container)
        if tab.ingest == "":
            tab.ingest = self._ports.new_secret()
            notify = self.on_ingest
            if notify is not None:
                notify(tab.id, tab.ingest)
        # A new terminal replaces the old one: a window that closed or crashed may have left its agent behind.
        tab.live_sidecar = ""
        if not self._stop_orphans(tab, attempts=_SCAN_ATTEMPTS):
            raise TabRequestError(_SCAN_FAILED)
        if self.history is not None and tab.work_session_id is not None:
            record = self.work_sessions.require(tab.project_id, tab.work_session_id, active=True)
            self.history.recover(record)
            _ = self.history.capture(record, tab.id, "begin", f"Begin: {tab.title}", worktree=tab.worktree)
        secret = self._ports.new_secret()
        tab.sidecar = secret
        path = self._ports.container_path(tab.container)
        self._forget_unwritten_session(tab)
        command = _launch_command(tab.agent, tab.session, fork=tab.fork)
        wired = self._wired_command(tab)
        if wired is not None:
            # The session flags follow the agent's own: `claude --settings f --resume s`, `codex -c x resume s`.
            command = (*wired, *command[_ONE:])
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
        if tab.agent is AgentKind.CURSOR:
            env[_ENV_SCREEN] = "1"
        argv = (self._ports.python_bin(), *_SIDECAR, *podman)
        return TerminalLaunchSpec(argv=argv, env=env, cwd=tab.worktree, scrollback_lines=_SCROLLBACK, title=tab.title)

    def _require_session_launch(self, tab: _Tab) -> None:
        if tab.work_session_id is None:
            return
        _ = self.work_sessions.require(tab.project_id, tab.work_session_id, active=True)
        for other in self._tabs.values():
            if other.id != tab.id and Path(other.worktree).resolve() == Path(tab.worktree).resolve() and (
                other.running or other.sidecar or other.live_sidecar
            ):
                raise DomainError(code=3025, name="WORKTREE_BUSY", retryable=True)
        for other in self._tabs.values():
            if other.id != tab.id and Path(other.worktree).resolve() == Path(tab.worktree).resolve() and (
                other.needs_process_check
            ):
                self._finish_session_actor(other, "recovery")

    def _forget_unwritten_session(self, tab: _Tab) -> None:
        """Claude names a session before it has written anything. Resuming that one is an error that ends the tab."""
        commands = self._commands
        if commands is None or tab.session is None or commands.session_exists(tab.agent, tab.session, tab.container):
            return
        tab.session = None
        tab.fork = False
        self._remember(tab)
        _ = self._updated(tab, {"agent_session_id"})

    def _wired_command(self, tab: _Tab) -> tuple[str, ...] | None:
        commands = self._commands
        if commands is None:
            return None
        return commands.command(tab.agent, tab.id, tab.ingest, tab.container, self._ports.ingest_port())

    def _agent_missing(self, kind: AgentKind) -> bool:
        if self._agents is None:
            return False
        return any(agent.kind is kind and not agent.available for agent in self._agents())

    def add_project_at(self, params: ProjectAddParams) -> ProjectView:
        catalog = self._require_catalog()
        if not params.root.startswith(("/", "~")):
            raise TabRequestError(_PATH_DENIED)
        before = catalog.views()
        record = catalog.add(params.root)
        if record is None:
            raise TabRequestError(_NOT_A_REPO)
        self._seed(record)
        if catalog.views() != before:
            self._projects_changed(catalog)
        return next(view for view in catalog.views() if str(view.id) == record.id)

    def remove_project(self, params: ProjectIdParams) -> EmptyResult:
        catalog = self._require_catalog()
        project_id = str(params.project_id)
        if not catalog.known(project_id):
            raise TabRequestError(_PROJECT)
        if any(tab.project_id == project_id for tab in self._tabs.values()):
            raise TabRequestError(_TAB_CONFLICT)
        catalog.remove(project_id)
        del self._projects[project_id]
        self._projects_changed(catalog)
        return EmptyResult()

    def set_active_project(self, params: ProjectIdParams) -> EmptyResult:
        catalog = self._require_catalog()
        if not catalog.known(str(params.project_id)):
            raise TabRequestError(_PROJECT)
        catalog.set_active(str(params.project_id))
        self._projects_changed(catalog)
        return EmptyResult()

    def _require_catalog(self) -> ProjectCatalog:
        if self._catalog is None:
            raise TabRequestError(_METHOD)
        return self._catalog

    def _projects_changed(self, catalog: ProjectCatalog) -> None:
        self._emit(ProjectsChanged(projects=catalog.views(), active_project=catalog.active))

    def started(self, params: StartedParams) -> EmptyResult:
        tab = self._require(str(params.tab_id))
        tab.pid = params.pid
        tab.running = True
        # A started process waits for a prompt. Only the agent's hooks say that the model works.
        tab.status = TabStatus.IDLE
        tab.since = self._clock.now()
        _ = self._refresh_attention(tab)
        self._remember(tab)
        _ = self._updated(tab, {"started", "status", "status_since", "attention"})
        return EmptyResult()

    def exited(self, params: ExitedParams) -> EmptyResult:
        tab = self._require(str(params.tab_id))
        tab.pid = None
        tab.running = False
        tab.needs_process_check = True
        tab.sidecar = ""
        tab.live_sidecar = ""
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
        if tab.work_session_id is not None:
            self._finish_session_actor(tab, "close-tab")
            if self._commands is not None:
                self._commands.release(tab.agent, tab.id, tab.container)
            self._forget(tab, drop_worktree=False)
            del self._tabs[tab.id]
            if self._focused == tab.id:
                self._focused = None
            self._emit(TabRemoved(tab_id=TabId(tab.id)))
            return CloseTabResult(closed=True, needs_decision=False, dirty_summary=None)
        summary = self._summary(tab)
        if summary is not None and params.decision is None:
            tab.dirty = True
            self._remember(tab)
            return CloseTabResult(closed=False, needs_decision=True, dirty_summary=summary)
        if summary not in {None, _IGNORED} and params.decision == _REMOVE:
            raise TabRequestError(_DIRTY)
        stopped = self._stop(tab)
        if self._commands is not None:
            self._commands.release(tab.agent, tab.id, tab.container)
        # A failed orphan scan may leave processes in the tree: keep it rather than delete files under them.
        decision = params.decision if stopped else _KEEP
        removed = self._finish_tree(tab, decision, discard_ignored=summary == _IGNORED)
        left = None if removed or decision == _KEEP or not tab.created_here else self._kept_summary(tab)
        self._forget(tab, drop_worktree=removed)
        del self._tabs[tab.id]
        if self._focused == tab.id:
            self._focused = None
        self._emit(TabRemoved(tab_id=TabId(tab.id)))
        return CloseTabResult(closed=True, needs_decision=False, dirty_summary=left)

    def restart(self, params: RestartParams) -> TabView:
        """Stop the old process and its orphans first, so a restart never leaves two agents in one tree.

        When the orphan scan fails, a kill fails, or a pid is still there, the known pid is stopped but the
        tab does not go idle: an orphan may still hold the tree, so the restart fails with CONTAINER_START_FAILED.
        """
        tab = self._require(str(params.tab_id))
        if tab.work_session_id is not None:
            self._finish_session_actor(tab, "restart")
            scanned = True
        else:
            scanned = self._stop(tab)
        tab.pid = None
        tab.running = False
        if not scanned:
            _ = self._updated(tab, {"started"})
            raise TabRequestError(_SCAN_FAILED)
        tab.status = TabStatus.IDLE
        tab.sidecar = ""
        tab.live_sidecar = ""
        tab.since = self._clock.now()
        if not params.resume:
            tab.session = None
        _ = self._refresh_attention(tab)
        self._remember(tab)
        return self._updated(tab, {"started", "status", "status_since", "attention", "agent_session_id"})

    def fork(self, params: TabIdParams) -> TabView:
        tab = self._require(str(params.tab_id))
        _require_fork(tab.agent)
        tab_id = self._ports.new_id()
        dest = tab.worktree
        branch = tab.branch
        created = False
        mode = WorkspaceMode.EXISTING_WORKTREE.value
        if self._fork_new:
            slug = f"s-{tab.agent.value}-fork"
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
            fork=True,
            running=False,
            repo=tab.repo,
            container=tab.container,
            created_here=created,
            created=now,
            mode=mode,
            ingest=self._ports.new_secret(),
            sidecar="",
            pid=None,
            work_session_id=tab.work_session_id if not created else None,
            isolated=tab.isolated if not created else False,
        )
        self._tabs[tab_id] = copy
        new_session = copy.work_session_id is None
        if new_session:
            copy.work_session_id = f"s-{tab_id}"
        self._remember(copy)
        if new_session and copy.work_session_id is not None:
            _ = self.work_sessions.add(WorkSession(
                id=copy.work_session_id, project_id=copy.project_id, title=copy.title,
                worktree=copy.worktree, branch=copy.branch, base_branch=copy.base,
                workspace_mode=copy.mode, status="active", created_at=now, updated_at=now,
            ))
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

    def _route_project_add(self, raw: bytes) -> ProjectView:
        return self.add_project_at(msgspec.json.decode(raw, type=ProjectAddParams))

    def _route_project_remove(self, raw: bytes) -> EmptyResult:
        return self.remove_project(msgspec.json.decode(raw, type=ProjectIdParams))

    def _route_project_active(self, raw: bytes) -> EmptyResult:
        return self.set_active_project(msgspec.json.decode(raw, type=ProjectIdParams))

    def _route_open(self, raw: bytes) -> TabView:
        return self.open_tab(msgspec.json.decode(raw, type=OpenTabParams))

    def _route_image_paste(self, raw: bytes) -> PreparedImagePaste:
        params = msgspec.json.decode(raw, type=PrepareImagePasteParams)
        tab = self._require(str(params.tab_id))
        path = self._ports.prepare_image(tab.worktree, tab.container, params.png)
        return PreparedImagePaste(path=path)

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
        slug = f"s-{params.worktree_name or params.agent.value}"[:41]
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

    def _finish_tree(self, tab: _Tab, decision: str | None, *, discard_ignored: bool) -> bool:
        """True only when the worktree directory was actually deleted."""
        if not tab.created_here:
            return False
        if decision == _KEEP:
            self._ports.release_worktree(tab.worktree)
            return False
        return self._ports.remove_worktree(tab.repo, tab.worktree, discard_ignored=discard_ignored)

    def _kept_summary(self, tab: _Tab) -> str:
        """Status after git refused a remove. `kept` means the fresh check still looks clean."""
        try:
            state = self._ports.worktree_state(tab.repo, tab.worktree)
        except TabRequestError:
            return "kept"
        if state == _CLEAN:
            return "kept"
        return state

    def _stop(self, tab: _Tab) -> bool:
        """Stop the tab process and its orphans. False when the orphan scan failed."""
        if tab.pid is not None:
            self._ports.stop_process(tab.pid)
            self._ports.pause(self._stop_s)
        return self._stop_orphans(tab)

    def _stop_orphans(self, tab: _Tab, *, attempts: int = 1) -> bool:
        """Stop this tab's container processes. False when a scan or a kill failed, or a pid is still there."""
        found = self._scan_orphans(tab, attempts)
        if found is None or not self._signal_orphans(tab, found):
            return False
        if not found:
            return True
        left = self._scan_orphans(tab, 1)
        return left == ()

    def _scan_orphans(self, tab: _Tab, attempts: int) -> tuple[int, ...] | None:
        for _attempt in range(attempts):
            try:
                return self._ports.orphans(tab.container, tab.id)
            except TabRequestError:
                continue
        return None

    def _signal_orphans(self, tab: _Tab, found: tuple[int, ...]) -> bool:
        # These pids belong to the container's namespace: on the host the same numbers are other processes.
        for pid in found:
            try:
                self._ports.stop_in_container(tab.container, pid)
            except TabRequestError:
                return False
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
        if saved.work_session_id is not None and self.work_sessions.at(saved.project_id, saved.worktree) is None:
            _ = self.work_sessions.add(WorkSession(
                id=saved.work_session_id, project_id=saved.project_id, title=saved.title,
                worktree=saved.worktree, branch=saved.branch, base_branch=saved.base_branch,
                workspace_mode=saved.workspace_mode, status="active",
                created_at=saved.created_at, updated_at=saved.since,
            ))
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
            work_session_id=saved.work_session_id,
            isolated=saved.isolated,
            needs_process_check=True,
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
            work_session_id=tab.work_session_id,
            isolated=tab.isolated,
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
            session_cost_usd=tab.cost_usd,
            agent_session_id=tab.session,
            pending_approvals=0,
            checkpoints=0,
            started=tab.running,
            mcp_bytes=tab.mcp_bytes or None,
            mcp_calls=tab.mcp_calls or None,
            work_session_title=(None if tab.work_session_id is None else
                self.work_sessions.require(tab.project_id, tab.work_session_id).title),
            work_session_id=tab.work_session_id,
            isolated=tab.isolated,
        )
