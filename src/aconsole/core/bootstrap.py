"""Composition root for `aconsoled`: one process, then idle shutdown."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import os
import signal
import socket as sockets
import stat
import struct
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum, auto
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, override

import msgspec
import uvicorn

from aconsole.contracts.app import AccountView, CloseTabParams, CommandResult, HealthResult, PlanView
from aconsole.contracts.common import AccountId, AgentKind, ClientRole, QuotaLevel, TabId
from aconsole.contracts.errors import ErrorData, ErrorObject, MessageTooLargeDetails, error_by_name
from aconsole.contracts.events import (
    Notify,
    PlanUpdated,
    QuotaUpdated,
    ResyncRequired,
    TabAdded,
    TabRemoved,
    TabUpdated,
    UiEvent,
)
from aconsole.contracts.ipc import InitializeParams, Notification, ProtocolLimits, Request, ResponseErr
from aconsole.contracts.messages import UiMessage
from aconsole.core.application.ports.events import EventSink
from aconsole.core.application.ports.git_repo import GitCommandError
from aconsole.core.application.services.approval_broker import ApprovalHold
from aconsole.core.application.services.approval_policy import ApprovalPolicyBook
from aconsole.core.application.services.checkpoints import CheckpointService
from aconsole.core.application.services.diff import DiffService
from aconsole.core.application.services.git import GitService
from aconsole.core.application.services.git_cleanup import GitCleanupService
from aconsole.core.application.services.git_merge import GitMergeService
from aconsole.core.application.services.git_ops import GitOperationService
from aconsole.core.application.services.git_rewrite import GitRewriteService
from aconsole.core.application.services.git_state import GitStateService
from aconsole.core.application.services.palette import NavigationSnapshot, PaletteService
from aconsole.core.application.services.projects import ProjectCatalog
from aconsole.core.application.services.quota_aggregator import QuotaAggregator
from aconsole.core.application.services.review import ReviewService
from aconsole.core.application.services.setup import SetupService
from aconsole.core.application.services.tabs import TabService
from aconsole.core.application.services.work_session_git import WorkSessionGit
from aconsole.core.application.services.work_session_history import WorkSessionHistory
from aconsole.core.domain.approval_policy import ApprovalPolicyChoice
from aconsole.core.domain.approvals import Approval, ApprovalBook
from aconsole.core.domain.clock import Clock
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.quota import quota_level
from aconsole.core.infrastructure.agents.claude.limits import ClaudeLimits
from aconsole.core.infrastructure.agents.codex.limits import CodexLimits
from aconsole.core.infrastructure.agents.codex.turns import TurnWatch
from aconsole.core.infrastructure.agents.cursor.limits import CursorLimits
from aconsole.core.infrastructure.agents.launch import HostAgentCommands
from aconsole.core.infrastructure.agents.live_limits import CachedAuth
from aconsole.core.infrastructure.agents.opencode.limits import OpenCodeLimits
from aconsole.core.infrastructure.agents.usage_log import UsageLog, claude_events, codex_events
from aconsole.core.infrastructure.codegraph.index import CodeGraphIndex
from aconsole.core.infrastructure.codegraph.runner import ExtractorRunner
from aconsole.core.infrastructure.git.checkpoints import GitCheckpoints
from aconsole.core.infrastructure.git.diff import GitDiff
from aconsole.core.infrastructure.git.history import GitHistory
from aconsole.core.infrastructure.git.operations import GitMutations
from aconsole.core.infrastructure.git.retention import drain_ref_deletions
from aconsole.core.infrastructure.git.watch import GitWatch
from aconsole.core.infrastructure.http.app import IngestApp, serve_config
from aconsole.core.infrastructure.http.approval import handle_permission
from aconsole.core.infrastructure.http.auth import TokenBook
from aconsole.core.infrastructure.http.consumer import OPENCODE_EVENT, IngestHandlers, consume
from aconsole.core.infrastructure.http.problem import HttpResponse, empty_json, json_response
from aconsole.core.infrastructure.http.routes import IngestItem, IngestQueue
from aconsole.core.infrastructure.ipc.framing import (
    MAX_MESSAGE_BYTES,
    MessageTooLargeError,
    decode_envelope,
    discard_frame,
    encode_line,
    read_frame,
)
from aconsole.core.infrastructure.ipc.ring import EventRing
from aconsole.core.infrastructure.ipc.server import allow_peer, peer_credentials, same_mnt_namespace
from aconsole.core.infrastructure.ipc.session import Connection
from aconsole.core.infrastructure.knowledge import KnowledgeService
from aconsole.core.infrastructure.mcp.server import TabTokens, beside_ingest, build_server, http_app
from aconsole.core.infrastructure.mcp.tools import PlanBoard, TabContext
from aconsole.core.infrastructure.persistence.cleanup import SweepResult, sweep
from aconsole.core.infrastructure.persistence.engine import checkpoint_wal, write_engine
from aconsole.core.infrastructure.persistence.models import ApprovalStatus
from aconsole.core.infrastructure.persistence.repositories.catalog import ApprovalRecord, ApprovalRepository
from aconsole.core.infrastructure.persistence.repositories.checkpoints import CheckpointDatabase
from aconsole.core.infrastructure.persistence.repositories.git_operations import GitOperationDatabase
from aconsole.core.infrastructure.persistence.repositories.knowledge import KnowledgeDatabase
from aconsole.core.infrastructure.persistence.repositories.plans import PlanDatabase
from aconsole.core.infrastructure.persistence.repositories.ref_deletions import RefDeletionQueue
from aconsole.core.infrastructure.persistence.repositories.tabs import TabDatabase
from aconsole.core.infrastructure.persistence.repositories.vault_files import VaultFileDatabase
from aconsole.core.infrastructure.persistence.repositories.work_sessions import (
    WorkSessionDatabase,
    WorkSessionHistoryDatabase,
)
from aconsole.core.infrastructure.persistence.startup import prepare_database, sqlite_url
from aconsole.core.infrastructure.persistence.writer import Writer
from aconsole.core.infrastructure.setup_checks import HostSetupFixes, HostSetupProbe, SetupTokens
from aconsole.core.infrastructure.tabs import HostAgents, HostRepos, HostTabs
from aconsole.core.infrastructure.vault.sync import VaultSync
lazy from aconsole.contracts.git import FactReviewView
lazy from aconsole.core.application.ports.knowledge_store import StoredFact
lazy from aconsole.core.domain.git_operation import GitOperationRecord
lazy from aconsole.core.domain.work_session import WorkSession

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.server.mcpserver import MCPServer
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

    from aconsole.contracts.app import AgentInfo, PlanView, TabView
    from aconsole.contracts.ipc import Envelope, EventEnvelope
    from aconsole.contracts.settings import Settings

_LOG = logging.getLogger(__name__)
IDLE_EXIT_S = 15.0
# DATA_MODEL §6 retention runs at start and then hourly.
SWEEP_EVERY_S = 3600.0
EXIT_REFUSED = 1
EXIT_USAGE = 2
_DIR_MODE = 0o700
_FILE_MODE = 0o600
_HOLDS = frozenset({ClientRole.UI, ClientRole.CLI})


def default_socket() -> str:
    """`$XDG_RUNTIME_DIR/aconsole/core.sock`, creating the directory."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    root = Path(runtime) if runtime else Path.home() / ".local" / "state"
    folder = root / "aconsole"
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder / "core.sock")


def parse_serve(argv: list[str]) -> tuple[Path, bool] | None:
    """`--serve [--socket PATH] [--allow-gil]`. None means the argv is not that command."""
    if not argv or argv[0] != "--serve":
        return None
    socket: Path | None = None
    allow_gil = False
    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg == "--allow-gil":
            allow_gil = True
            index += 1
            continue
        if arg != "--socket" or index + 1 >= len(argv):
            return None
        value = argv[index + 1]
        if not value or value.startswith("--"):
            return None
        socket = Path(value)
        index += 2
    if socket is None:
        socket = Path(default_socket())
    return socket, allow_gil


def extension_modules() -> tuple[str, ...]:
    """Loaded extension modules. A candidate list when the GIL turned itself on."""
    names: list[str] = []
    for name, module in sys.modules.items():
        if not hasattr(module, "__file__"):
            continue
        file = module.__file__
        if file is not None and file.endswith((".so", ".pyd")):
            names.append(name)
    names.sort()
    return tuple(names)


def gil_enabled() -> bool:
    """CPython's free-threading probe. The leading underscore is the public name."""
    return sys._is_gil_enabled()  # noqa: SLF001  # pyright: ignore[reportPrivateUsage] -- stdlib probe


def gil_refusal(*, allow_gil: bool, enabled: bool, candidates: tuple[str, ...]) -> str | None:
    """Why startup must stop, or None when free-threading is on or explicitly allowed."""
    if allow_gil or not enabled:
        return None
    listed = ", ".join(candidates) if candidates else "(none)"
    return f"GIL is enabled; refusing to start. Candidates: {listed}"


def acquire_lock(path: Path) -> int | None:
    """Exclusive non-blocking flock. None means another process already holds it.

    The descriptor is close-on-exec so a child does not keep the lock after this process exits.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(_DIR_MODE)
    # flock follows the open file description. CLOEXEC drops it across exec.
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, _FILE_MODE)
    try:
        os.fchmod(descriptor, _FILE_MODE)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return None
    return descriptor


_RECOVERY_BYTES = 256 * 1024
_MAX_PORT = 65535


class _Recovery(msgspec.Struct):
    """Runtime credentials survive a core restart, never a reboot or a database export."""

    port: int = 0
    tokens: dict[str, str] = msgspec.field(default_factory=dict)


def _read_recovery(path: Path) -> _Recovery:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return _Recovery()
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            message = "unsafe core recovery file"
            raise PermissionError(message)
        data = stream.read(_RECOVERY_BYTES + 1)
    if len(data) > _RECOVERY_BYTES:
        message = "core recovery file is too large"
        raise ValueError(message)
    recovered = msgspec.json.decode(data, type=_Recovery)
    if not 0 <= recovered.port <= _MAX_PORT or any(not key or not token for key, token in recovered.tokens.items()):
        message = "invalid core recovery credentials"
        raise ValueError(message)
    return recovered


def _write_recovery(path: Path, recovered: _Recovery) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".core-recovery-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            _ = stream.write(msgspec.json.encode(recovered))
            stream.flush()
            os.fsync(stream.fileno())
        _ = Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def release_lock(descriptor: int) -> None:
    """Unlock and close. The next process can take the lock."""
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def credentials(sock: object) -> tuple[int, int]:
    """Peer pid and uid from the kernel, or (-1, -1) when the socket has none."""
    try:
        return peer_credentials(sock)
    except AttributeError, OSError, TypeError, struct.error:
        return -1, -1


def credentials_uid(sock: object) -> int:
    """Peer uid, or -1 when the socket has no credentials."""
    return credentials(sock)[1]


def initialize_role(frame: bytes) -> ClientRole | None:
    """Role carried by an initialize request. None for every other frame."""
    try:
        envelope = decode_envelope(frame)
    except msgspec.DecodeError:
        return None
    if not isinstance(envelope, Request) or envelope.method != "initialize":
        return None
    try:
        params = msgspec.json.decode(envelope.params, type=InitializeParams)
    except msgspec.DecodeError:
        return None
    return params.role


def _too_large(encoder: msgspec.json.Encoder, limit: int, request_id: int | None = None) -> bytes:
    spec = error_by_name("MESSAGE_TOO_LARGE")
    error = ErrorObject(
        code=spec.code,
        message=spec.name,
        data=ErrorData(
            reason=spec.name,
            domain="aconsole.ipc",
            retryable=spec.retryable,
            user_message_key="error.message_too_large",
            details=MessageTooLargeDetails(limit=limit),
        ),
    )
    return encode_line(encoder, ResponseErr(id=request_id, error=error))


def _windows(account: AccountView) -> tuple[tuple[str, float | None, datetime | None], ...]:
    return tuple((window.key, window.used_pct, window.resets_at) for window in account.quota)


@dataclass
class _IpcTimes:
    """How many requests were answered, and the slowest of the last minute or two."""

    answered: int = 0
    in_window: int = 0
    slowest_s: float = 0.0
    previous_slowest_s: float = 0.0

    def note(self, seconds: float) -> None:
        self.answered += 1
        self.in_window += 1
        self.slowest_s = max(self.slowest_s, seconds)

    def next_window(self) -> None:
        self.in_window = 0
        self.previous_slowest_s = self.slowest_s
        self.slowest_s = 0.0

    @property
    def recent_slowest_s(self) -> float:
        return max(self.slowest_s, self.previous_slowest_s)


class _Route(Enum):
    """How a frame runs relative to the ones read before it."""

    NOW = auto()  # At once on the loop: quick, and a person or an agent is waiting on it.
    APART = auto()  # Beside everything else: slow, and nothing depends on its order.
    GIT = auto()  # Beside everything else too, waiting for one of the slots git runs in.
    TURN = auto()  # After the frames before it, so that tab changes stay in order.


_HEALTH_EVERY_S = 60.0
_MS_PER_S = 1000
_IMPACT = "knowledge/impact"
_GIT_PREFIX = "git/"
_MAX_GIT_READS = 4
# `quota/refresh` only sets asyncio events, which belong to the loop: it is answered there, never in a worker.
_NOW_METHODS = frozenset({"approval/decide", "approval/releaseToNative", "core/hold", "app/health", "quota/refresh"})
_CANCEL = "$/cancelRequest"
_MAX_QUEUED = ProtocolLimits().max_inflight_requests
_MAX_APART = 4
_MAX_IMPACTS = 2


_SIDECAR_GRACE_S = 1.0


def _process_alive(pid: int) -> bool:
    """Not gone and not a zombie waiting for its parent."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
    except OSError:
        return False
    return stat.rpartition(")")[2].split()[0] not in {"Z", "X"}


def _route(envelope: Envelope, *, ready: bool) -> _Route:
    if not ready:
        return _Route.TURN
    if isinstance(envelope, Notification):
        return _Route.NOW if envelope.method == _CANCEL else _Route.TURN
    if isinstance(envelope, Request) and envelope.method in _NOW_METHODS:
        return _Route.NOW
    if isinstance(envelope, Request) and envelope.method == _IMPACT:
        return _Route.APART
    if isinstance(envelope, Request) and envelope.method.startswith(_GIT_PREFIX):
        return _Route.GIT
    return _Route.TURN


_CLAUDE_CONTAINER = "agent-claude"
_CLAUDE_LOGS = ".claude/projects"
_CODEX_LOGS = ".codex/sessions"
_TOKEN_DAYS = 7
_CODEX_CONTAINER = "agent-codex"
_CODEX_POLL = "codex-poll"
_CODEX_POLL_S = 120.0
# A stopped container gives nothing, and opening a tab starts it: the limits should not wait two minutes after that.
_CODEX_RETRY_S = 30.0
_CODEX_REPLY_LOGGED = 300
_CLAUDE_POLL_S = 60.0
_CURSOR_CONTAINER = "agent-cursor"
# Cursor counts usage per billing cycle, and its numbers move with requests: every two minutes is enough.
_CURSOR_POLL_S = 120.0
_OPENCODE_CONTAINER = "agent-opencode"
_OPENCODE_POLL_S = 120.0
_CODEX_TURN_POLL_S = 1.0
_ACCOUNT_RESEND_S = 30.0
# Agents whose account ids come from their own replies: a placeholder with another id would stay beside the real card.
_OWN_ACCOUNTS = frozenset({AgentKind.CLAUDE, AgentKind.CODEX})


def codex_poll_delay(body: bytes) -> float:
    """How long until the next question. An answer, even a refusal, will not change soon; silence may."""
    return _CODEX_POLL_S if body else _CODEX_RETRY_S


def tokens_since(account: AccountView, now: datetime) -> datetime:
    """When the account's weekly window began: tokens are counted over the same days as the limit."""
    resets = [window.resets_at for window in account.quota if window.key in {"7d", "week"} and window.resets_at]
    return (resets[0] if resets else now) - timedelta(days=_TOKEN_DAYS)


def is_connected(agents: tuple[AgentInfo, ...], kind: AgentKind) -> bool:
    """The container exists and the CLI in it has a login. Anything else has no limits to ask about or show."""
    return any(agent.kind is kind and agent.available and agent.logged_in is True for agent in agents)


def shown_accounts(agents: tuple[AgentInfo, ...], observed: tuple[AccountView, ...]) -> tuple[AccountView, ...]:
    """The accounts for the snapshot: only connected agents', even if an earlier login left numbers behind."""
    connected = tuple(account for account in observed if is_connected(agents, account.agent))
    return (*connected, *accounts_without_limits(agents, connected))


def accounts_without_limits(
    agents: tuple[AgentInfo, ...], observed: tuple[AccountView, ...] = (),
) -> tuple[AccountView, ...]:
    """A connected agent with no numbers yet, or none at all, still gets its card, which says they are unknown.

    Claude and Codex name their accounts in their replies, so their cards come with their first numbers.
    """
    heard = {account.agent for account in observed}
    return tuple(
        AccountView(id=AccountId(f"{agent.kind.value}:local"), agent=agent.kind, plan_label="", quota=())
        for agent in agents
        if is_connected(agents, agent.kind) and agent.kind not in heard and agent.kind not in _OWN_ACCOUNTS
    )


class _PublishedEvents(EventSink):
    """The diff service's events go where the tab book's go."""

    def __init__(self, publish: Callable[[UiEvent], None]) -> None:
        super().__init__()
        self._publish: Callable[[UiEvent], None] = publish

    @override
    def emit(self, event: UiEvent) -> None:
        self._publish(event)


class Daemon:
    """The core process. A second instance refuses the lock. Idle clients shut it down."""

    def __init__(  # noqa: PLR0913, PLR0915 -- daemon configuration and injection points
        self,
        socket: Path,
        *,
        idle_s: float = IDLE_EXIT_S,
        live_tabs: int = 0,
        frame_limit: int = MAX_MESSAGE_BYTES,
        database: Path | None = None,
        setup_service: SetupService | None = None,
        host: HostTabs | None = None,
    ) -> None:
        if idle_s < 0:
            message = "idle seconds cannot be negative"
            raise ValueError(message)
        if live_tabs < 0:
            message = "live tab count cannot be negative"
            raise ValueError(message)
        if frame_limit < 1:
            message = "frame limit must be positive"
            raise ValueError(message)
        self.socket: Path = socket
        self.idle_s: float = idle_s
        self._live_tabs: int = live_tabs
        self._frame_limit: int = frame_limit
        self._impacts: asyncio.Semaphore = asyncio.Semaphore(_MAX_IMPACTS)
        # Git can take seconds on a big tree. It runs beside the tab worker, so approvals and tab calls never wait.
        self._git_slots: asyncio.Semaphore = asyncio.Semaphore(_MAX_GIT_READS)
        self._ipc: _IpcTimes = _IpcTimes()
        self._health: asyncio.Task[None] | None = None
        self._holders: int = 0
        self._listening: bool = False
        self._closing: bool = False
        self._stop_requested: bool = False
        self._stopped: asyncio.Event | None = None
        self._server: asyncio.AbstractServer | None = None
        self._idle: asyncio.Task[None] | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._ring: EventRing = EventRing()
        self.approvals: ApprovalHold = ApprovalHold(ApprovalBook(), publish=self._approval_event,
            notify=self._approval_notify, audit=self._approval_audit, now=lambda: datetime.now(UTC))
        self._host: HostTabs = HostTabs(socket_path=str(socket)) if host is None else host
        self.tabs: TabService = TabService(Clock(), self._host, listener=self._from_worker)
        # The loop consumes immutable policy and loop-published paths without queueing behind tab/Git work.
        self._permission_settings: Settings = self.tabs.current_settings()
        self._permission_worktrees: dict[str, str] = {}
        self._permission_projects: dict[str, str] = {}
        self._approval_policy: ApprovalPolicyBook = ApprovalPolicyBook()
        self.tabs.on_ingest = self._ingest_minted
        self._catalog: ProjectCatalog = ProjectCatalog(
            HostRepos(), new_id=self._host.new_id, now=lambda: datetime.now(UTC),
        )
        self.tabs.use_projects(self._catalog)
        self._agent_commands: HostAgentCommands = HostAgentCommands(
            self._host.agent_home, self._opencode_heard, codex_hooks=self._host.codex_hooks,
            codex_issue=self._codex_hook_issue,
            approval_choice=self.tabs.approval_choice,
            claude_version=self._host.claude_version,
        )
        self._agents_source: Callable[[], tuple[AgentInfo, ...]] = HostAgents(
            self._host.inspect_container, signed_in=self._host.signed_in,
            capabilities=self._agent_commands.capabilities,
        ).agents
        self.tabs.use_agents(self._agents_source)
        self.tabs.use_commands(self._agent_commands)
        self._setup_limits()
        self.review: ReviewService = ReviewService(
            lambda: self.tabs,
            CheckpointService(GitCheckpoints()),
            DiffService(GitDiff(), _PublishedEvents(self._from_worker)),
            emit=self._from_worker,
        )
        history = GitHistory()
        self._git_watch: GitWatch = GitWatch(self._git_heard)
        mutations = GitMutations()
        self.git: GitService = GitService(GitStateService(history, lambda: self.tabs), emit=self._from_worker)
        self.git.state.session_at = self._session_at
        self.git.watcher = self._git_watch
        self.git.state.node_held_by = self._node_held_by
        self.git.operations = GitOperationService(
            self.git.state,
            mutations,
            now=lambda: datetime.now(UTC),
            tab_safety=self._keep_before_change,
            changed=self._git_changed,
        )
        self.tabs.workspace_lock = self.git.operations.lock_of
        self.tabs.history = WorkSessionHistory(GitCheckpoints(), history, lambda: datetime.now(UTC))
        self.tabs.history.actor_context = self._session_actor_context
        self.git.state.session_by_id = self.tabs.work_sessions.require
        self.git.state.snapshots = self.tabs.history.records
        self.tabs.inspect_workspace = self._inspect_session_workspace
        self.git.merges = GitMergeService(
            self.git.state,
            self.git.operations,
            mutations,
            now=lambda: datetime.now(UTC),
            changed=self._git_changed,
        )
        self.git.merges.follow_up = self._after_merge
        self.git.cleanup = GitCleanupService(
            self.git.state,
            self.git.operations,
            self.git.merges,
            mutations,
            now=lambda: datetime.now(UTC),
            facts=self._facts_to_check,
            close_tab=self._close_for_cleanup,
            publish_facts=self._publish_facts,
            changed=self._git_changed,
        )
        self.git.cleanup.removed = self._session_workspace_removed
        self.git.rewrite = GitRewriteService(
            self.git.state,
            self.git.operations,
            self.git.merges,
            mutations,
            now=lambda: datetime.now(UTC),
            deliver=self._send_to_tab,
            changed=self._git_changed,
        )
        self.tabs.session_git = WorkSessionGit(
            sessions=self.tabs.work_sessions, history=self.tabs.history, state=self.git.state,
            operations=self.git.operations, merges=self.git.merges, writer=mutations,
            close_tab=self._close_session_worker,
            now=lambda: datetime.now(UTC),
        ).dispatch
        self.setup_service: SetupService = setup_service or SetupService(
            probe=HostSetupProbe(), fixes=HostSetupFixes(), tokens=SetupTokens()
        )
        self._encoder: msgspec.json.Encoder = msgspec.json.Encoder()
        # Tab calls block (podman, git, the 3 s stop wait): one worker thread runs them in order, off the loop.
        self._worker: ThreadPoolExecutor | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connections: dict[Connection, asyncio.StreamWriter] = {}
        # Hooks reach the core over HTTP on 127.0.0.1 (INGEST_HTTP). Off by default so tests stay socket-only.
        self.serve_ingest: bool = False
        self.ingest_port: int | None = None
        self._tokens: TokenBook = TokenBook()
        self._recovery: _Recovery | None = None
        self._recovery_path: Path = self.socket.with_suffix(".recovery.json")
        self._palette_plans: dict[str, PlanView] = {}
        self._palette_plan_lock: Lock = Lock()
        self._plans: PlanBoard = PlanBoard(checked_completion=True)
        self._plans.on_changed = self._plan_changed
        # Off the loop and off the tab worker: files can be slow, and an edit must not wait for a tab call.
        self._vault_thread: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aconsole-vault")
        self.vault: VaultSync = VaultSync(self._plans)
        self.vault.on_edit = self._vault_edited
        self._plans.note_ref = self.vault.note_ref
        self.vault_default: Path | None = None
        self.tabs.on_settings_changed = self._use_vault
        code_graph = CodeGraphIndex(
            ExtractorRunner(), self._project_root, languages=lambda: self.tabs.current_settings().codegraph_languages,
        )
        self._plans.code_graph = code_graph
        self.knowledge: KnowledgeService = KnowledgeService(self._plans, self.vault, code_graph)
        self.palette: PaletteService = PaletteService(
            self._palette_snapshot, self._palette_note_uri, self.vault.note_ref,
        )
        self._mcp_tokens: TabTokens = TabTokens()
        self._mcp: MCPServer = build_server(
            self._plans,
            # No tab: a request without a token may list the tools, and every tool refuses it.
            TabContext(tab_id="", project_id="", worktree=None, agent="claude"),
            self._mcp_sent,
        )
        self._ingest_queue: IngestQueue = IngestQueue()
        self._ingest_ready: asyncio.Event | None = None
        self._ingest_loop: asyncio.AbstractEventLoop | None = None
        self._ingest_tasks: list[asyncio.Task[None]] = []
        self._uvicorn: uvicorn.Server | None = None
        self.database: Path | None = database
        self.sweep_every_s: float = SWEEP_EVERY_S
        self._engine: Engine | None = None
        self._writer: Writer | None = None
        self._sweeper: asyncio.Task[None] | None = None
        # Checkpoint refs whose rows the sweep removed. Deleting the git refs belongs to the checkpoint service (A4).
        self.dropped_refs: list[str] = []

    def _setup_limits(self) -> None:
        board = QuotaAggregator()
        auth = CachedAuth(lambda argv: self._host.agent_output(_CLAUDE_CONTAINER, argv))
        self._claude_limits: ClaudeLimits = ClaudeLimits(auth, board, lambda: datetime.now(UTC))
        self._codex_limits: CodexLimits = CodexLimits(board, lambda: datetime.now(UTC))
        self._cursor_limits: CursorLimits = CursorLimits(lambda: datetime.now(UTC))
        self._opencode_limits: OpenCodeLimits = OpenCodeLimits(lambda: datetime.now(UTC))
        self._claude_tokens: UsageLog = UsageLog(
            lambda: self._log_folder(_CLAUDE_CONTAINER, _CLAUDE_LOGS), claude_events,
        )
        self._codex_tokens: UsageLog = UsageLog(lambda: self._log_folder(_CODEX_CONTAINER, _CODEX_LOGS), codex_events)
        self._limits_events: dict[str, asyncio.Event] = {}
        self._accounts: dict[str, AccountView] = {}
        self._quota_thresholds: tuple[float, float] = (80, 95)
        self._account_sent: dict[str, float] = {}
        self._claude_usage_due: asyncio.Event = asyncio.Event()
        self._notices: set[asyncio.Task[None]] = set()
        self.tabs.use_accounts(lambda: shown_accounts(self._agents_source(), tuple(self._accounts.values())))

    @property
    def lock_path(self) -> Path:
        """Sibling of the socket: `core.sock` → `core.lock`."""
        return self.socket.with_name("core.lock")

    def request_stop(self) -> None:
        """Ask `serve` to return. Safe from a signal handler on this thread."""
        self._stop_requested = True
        stopped = self._stopped
        if stopped is not None:
            stopped.set()

    def set_live_tabs(self, count: int) -> None:
        """How many tabs still need the core. Call this on the asyncio thread."""
        if count < 0:
            message = "live tab count cannot be negative"
            raise ValueError(message)
        was_quiet = self._quiet()
        self._live_tabs = count
        if self._listening:
            self._on_presence(was_quiet=was_quiet)

    async def serve(self) -> int:
        """0 after a clean stop. 1 when another process holds the lock. The socket is left untouched then."""
        self._closing = False
        self._listening = False
        self._holders = 0
        self._writers.clear()
        self._stopped = asyncio.Event()
        if self._stop_requested:
            self._stopped.set()
        descriptor = acquire_lock(self.lock_path)
        if descriptor is None:
            return EXIT_REFUSED
        self._loop = asyncio.get_running_loop()
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aconsole-tabs")
        self._vault_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aconsole-vault")
        git_watch: asyncio.Task[None] | None = None
        try:
            await self._open_database()
            await self._restore_tabs()
            self._restore_credentials()
            await self._open_ingest()
            await self._listen()
            self._listening = True
            git_watch = asyncio.create_task(self._watch_git())
            if self._quiet() and not self._stopped.is_set():
                self._arm_idle()
            _ = await self._stopped.wait()
        finally:
            self._git_watch.stop()
            if git_watch is not None:
                _ = git_watch.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await git_watch
            self._closing = True
            self._listening = False
            self._cancel_idle()
            self.approvals.release_pending()
            try:
                await self._close_server()
                await self._close_ingest()
                await asyncio.to_thread(self._worker.shutdown, wait=True, cancel_futures=True)
                await asyncio.to_thread(self._stop_vault)
                await self._close_database()
            finally:
                await asyncio.to_thread(self._worker.shutdown, wait=True, cancel_futures=True)
                self._worker = None
                release_lock(descriptor)
        return 0

    def _restore_credentials(self) -> None:
        """Keep old hooks and MCP sessions valid until an explicit agent relaunch rotates them."""
        if self.database is not None and self.serve_ingest:
            self._recovery = _read_recovery(self._recovery_path)
            tabs = self.tabs.snapshot().tabs
            self._recovery.tokens = {
                str(tab.id): token for tab in tabs
                if (token := self._recovery.tokens.get(str(tab.id))) is not None
            }
            for tab in tabs:
                token = self._recovery.tokens.get(str(tab.id))
                if token is not None:
                    self._register_tab(tab, token)

    def _codex_hook_issue(self, tab_id: str, body: str) -> None:
        self._from_worker(Notify(tab_id=TabId(tab_id), title=UiMessage.CODEX_HOOKS_UNAVAILABLE, body=body,
            priority="normal", episode_id=f"codex-hooks:{tab_id}"))

    def _from_worker(self, event: UiEvent) -> None:
        """Tab listener. Runs on the worker; the live count is read there, where the tab book is consistent."""
        live = self.tabs.running_count()
        token = self.tabs.ingest_token(str(event.tab.id)) if isinstance(event, TabAdded) else None
        loop = self._loop
        if loop is not None and not loop.is_closed():
            _ = loop.call_soon_threadsafe(self._publish, event, live, token)
        if isinstance(event, TabUpdated):
            self.review.on_event(event)
        self.git.on_event(event)

    def _project_root(self, project_id: str) -> Path | None:
        found = next((item for item in self._catalog.views() if str(item.id) == project_id), None)
        return None if found is None else Path(found.root)

    def _vault_edited(self, plan_id: str) -> None:
        self._to_vault(self._publish_notes, plan_id)

    def _to_vault[T](self, work: Callable[[T], None], argument: T) -> None:
        """A change after the core began to stop is in the database already; its note follows on the next start."""
        with contextlib.suppress(RuntimeError):
            _ = self._vault_thread.submit(work, argument)

    def _publish_notes(self, plan_id: str) -> None:
        """One failed note must not stop the thread that writes the rest."""
        try:
            self.vault.publish(plan_id)
        except Exception:  # noqa: BLE001 -- logged; the thread keeps serving other plans
            _LOG.exception("the notes of plan %s could not be synced", plan_id)

    def _vault_root(self, settings: Settings) -> Path | None:
        return Path(settings.vault_path) if settings.vault_path else self.vault_default

    def _use_vault(self, settings: Settings) -> None:
        self._permission_settings = settings
        self._to_vault(self._switch_vault, self._vault_root(settings))
        loop = self._loop
        if loop is not None:
            _ = loop.call_soon_threadsafe(
                self._use_quota_thresholds, settings.quota_warn_pct, settings.quota_critical_pct,
            )

    def _use_quota_thresholds(self, warn: float, critical: float) -> None:
        if (warn, critical) == self._quota_thresholds:
            return
        self._quota_thresholds = (warn, critical)
        self._account_sent.clear()
        for account in tuple(self._accounts.values()):
            self._account_observed(QuotaUpdated(account=account))

    def _stop_vault(self) -> None:
        """Queued notes finish before the writer stops: their synced state goes through it."""
        self.vault.stop()
        self._vault_thread.shutdown(wait=True)

    def _switch_vault(self, root: Path | None) -> None:
        try:
            self.vault.use_root(root)
            self.vault.publish_all()
        except Exception:  # noqa: BLE001 -- logged; the thread keeps serving other plans
            _LOG.exception("the vault at %s could not be used", root)

    def _plan_changed(self, plan: PlanView) -> None:
        """Any thread: MCP tools run on the loop, `plan/setNodeStatus` on the worker."""
        with self._palette_plan_lock:
            self._palette_plans[str(plan.id)] = plan
        self._to_vault(self._publish_notes, str(plan.id))
        loop = self._loop
        if loop is not None and not loop.is_closed():
            _ = loop.call_soon_threadsafe(self._publish, PlanUpdated(plan=plan), self._live_tabs, None)

    def _ingest_minted(self, tab_id: str, token: str) -> None:
        """A restored tab got its token from `tab/launchSpec`, on the worker. The hooks start only after that reply."""
        view = next((tab for tab in self.tabs.snapshot().tabs if str(tab.id) == tab_id), None)
        loop = self._loop
        if view is not None and loop is not None and not loop.is_closed():
            _ = loop.call_soon_threadsafe(self._register_tab, view, token)

    def _register_tab(self, tab: TabView, token: str) -> None:
        tab_id = str(tab.id)
        self._permission_worktrees[tab_id] = tab.worktree_path
        self._permission_projects[tab_id] = str(tab.project_id)
        if self._recovery is not None and self._recovery.tokens.get(tab_id) != token:
            self._recovery.tokens[tab_id] = token
            _write_recovery(self._recovery_path, self._recovery)
        self._tokens.register(tab_id, token)
        worktree = tab.worktree_path
        self._mcp_tokens.register(
            tab_id,
            token,
            TabContext(
                tab_id=tab_id,
                project_id=str(tab.project_id),
                worktree=Path(worktree) if worktree else None,
                agent=str(tab.agent),
            ),
        )

    def _approval_event(self, event: UiEvent) -> None:
        self._publish(event, self._live_tabs, None)

    def _approval_notify(self, approval: Approval) -> None:
        self._approval_event(Notify(tab_id=TabId(approval.tab_id), title=UiMessage.APPROVAL_REQUIRED,
            body=approval.command or approval.tool, priority="high", episode_id=approval.id))

    def _approval_audit(self, approval: Approval) -> None:
        writer = self._writer
        if writer is None:
            return
        record = ApprovalRecord(id=approval.id, tab_id=approval.tab_id, agent_session_id=approval.session_id,
            tool=approval.tool, input_sha256=approval.input_sha256, nonce=approval.nonce,
            summary=(f"{approval.reason}: " if approval.reason else "") + (approval.command or approval.tool),
            risk=[tag.value for tag in approval.risk],
            status=ApprovalStatus(approval.phase.value), decided_by=approval.by.value if approval.by else None,
            requested_at=approval.requested_at, decided_at=datetime.now(UTC), expires_at=approval.expires_at)
        future = writer.submit(lambda session: ApprovalRepository().add(session, record))
        future.add_done_callback(self._audit_done)

    @staticmethod
    def _audit_done(future: asyncio.Future[object]) -> None:
        if not future.cancelled() and future.exception() is not None:
            _LOG.error("approval audit failed: %s", future.exception())

    async def _permission(self, item: IngestItem) -> HttpResponse:
        settings = self._permission_settings
        inline = settings.approvals_mode != "native" and self._has_approval_ui() and not self._stop_requested
        worktree = self._permission_worktrees.get(item.tab_id)
        if worktree is None:
            return json_response(empty_json())
        return await handle_permission(
            item, self.approvals, hold_seconds=settings.approval_hold_seconds, worktree=worktree,
            choice=self._approval_policy.choice_for(self._permission_projects.get(item.tab_id))
                if item.kind == "claude.hook" and self._agent_commands.policy_ready(item.tab_id)
                else ApprovalPolicyChoice(),
            inline=inline,
        )

    def _has_approval_ui(self) -> bool:
        return any(connection.ready and connection.role is ClientRole.UI and
            "approvals.inline" in connection.capabilities for connection in self._connections)

    def _publish(self, event: UiEvent, live: int, token: str | None) -> None:
        """Number the event once, send it to every subscribed connection, and keep tokens and liveness current."""
        if isinstance(event, (TabAdded, TabUpdated)):
            self._permission_worktrees[str(event.tab.id)] = event.tab.worktree_path
            self._permission_projects[str(event.tab.id)] = str(event.tab.project_id)
        if isinstance(event, TabAdded) and token is not None:
            self._register_tab(event.tab, token)
        if isinstance(event, TabRemoved):
            _ = self._permission_worktrees.pop(str(event.tab_id), None)
            _ = self._permission_projects.pop(str(event.tab_id), None)
            self.approvals.drop_tab(str(event.tab_id))
            self._tokens.drop(str(event.tab_id))
            self._mcp_tokens.drop(str(event.tab_id))
            if self._recovery is not None:
                _ = self._recovery.tokens.pop(str(event.tab_id), None)
                _write_recovery(self._recovery_path, self._recovery)
        item = self._ring.append(event, datetime.now(UTC))
        for connection, writer in tuple(self._connections.items()):
            self._deliver(connection, writer, item)
        if self._listening:
            self.set_live_tabs(live)

    def _deliver(self, connection: Connection, writer: asyncio.StreamWriter, item: EventEnvelope) -> None:
        if writer.is_closing():
            return
        for frame in connection.deliver(item):
            if len(frame) > self._frame_limit + 1:
                # The client sees a seq gap and pulls a snapshot instead of losing its connection.
                _LOG.warning("event %d is %d bytes, over the limit; not sent", item.seq, len(frame))
                continue
            writer.write(frame)
        if writer.transport.get_write_buffer_size() == 0:
            connection.drained()

    async def _in_worker[T](self, call: Callable[[], T]) -> T:
        worker = self._worker
        if worker is None:
            return call()
        return await asyncio.get_running_loop().run_in_executor(worker, call)

    async def _open_ingest(self) -> None:
        """Bind 127.0.0.1 on a free port, serve the ingest app, and start the consumer."""
        if not self.serve_ingest:
            return
        ready = asyncio.Event()
        self._ingest_ready = ready
        self._ingest_loop = asyncio.get_running_loop()

        async def wake(item: IngestItem) -> None:
            del item
            ready.set()

        app = beside_ingest(
            IngestApp(self._tokens, self._ingest_queue, observer=wake, permission_handler=self._permission),
            http_app(self._mcp),
            self._mcp_tokens,
        )
        listener = sockets.socket(sockets.AF_INET, sockets.SOCK_STREAM)
        listener.setsockopt(sockets.SOL_SOCKET, sockets.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", self._recovery.port if self._recovery is not None else 0))
        port = int(listener.getsockname()[1])  # pyright: ignore[reportAny] -- AF_INET address is (host, port)
        server = uvicorn.Server(serve_config(app, port=port))
        self._uvicorn = server
        self.ingest_port = port
        if self._recovery is not None:
            self._recovery.port = port
            _write_recovery(self._recovery_path, self._recovery)
        self._host.set_ingest_port(port)
        self._ingest_tasks = [
            asyncio.create_task(server.serve(sockets=[listener])),
            asyncio.create_task(
                consume(
                    self._ingest_queue, ready, self._apply_hook,
                    IngestHandlers(
                        on_prompt=self._prompt_sent, on_session=self._reported_session, on_cost=self._session_cost,
                        on_loss=self._ingest_lost,
                    ),
                ),
            ),
            asyncio.create_task(self._poll_codex_limits()),
            asyncio.create_task(self._poll_claude_limits()),
            asyncio.create_task(self._poll_cursor_limits()),
            asyncio.create_task(self._poll_opencode_limits()),
            asyncio.create_task(self._watch_codex_turns()),
        ]
        # uvicorn exposes only a `started` flag, no event to wait on.
        while not server.started and not self._ingest_tasks[0].done():  # noqa: ASYNC110
            await asyncio.sleep(0.01)

    async def _ingest_lost(self, tab_id: str, count: int) -> None:
        _LOG.warning("ingest lost/coalesced %d events for %s", count, tab_id)
        if tab_id not in self._permission_worktrees:
            return
        self._approval_event(Notify(
            tab_id=TabId(tab_id), title="История событий агента может быть неполной",
            body="Состояние обновлено после перегрузки. Проверьте историю действий агента.",
            priority="high", episode_id=f"ingest-loss:{tab_id}:{self._ring.current_seq}",
        ))
        self._approval_event(ResyncRequired(reason="backpressure"))

    async def _apply_hook(self, tab_id: str, event_name: str) -> None:
        _ = await self._in_worker(lambda: self.tabs.apply_hook(tab_id, event_name))
        if event_name == "Stop":
            self._claude_usage_due.set()

    def _mcp_sent(self, tab_id: str, size: int) -> None:
        """From an MCP tool call. Tabs change on the worker."""
        worker = self._worker
        if worker is None:
            self.tabs.mcp_sent(tab_id, size)
            return
        try:
            _ = worker.submit(self.tabs.mcp_sent, tab_id, size)
        except RuntimeError:
            return  # the worker is shutting down with the core

    async def _session_cost(self, tab_id: str, usd: float) -> None:
        await self._in_worker(lambda: self.tabs.session_cost(tab_id, usd))

    async def _reported_session(self, tab_id: str, session: str) -> None:
        await self._in_worker(lambda: self.tabs.reported_session(tab_id, session))

    def _opencode_heard(self, tab_id: str, body: bytes) -> None:
        """From the stream's thread. The queue and its wake-up belong to the loop."""
        loop = self._ingest_loop
        ready = self._ingest_ready
        if loop is None or ready is None:
            return

        def queue() -> None:
            self._ingest_queue.push(IngestItem(tab_id=tab_id, kind=OPENCODE_EVENT, body=body))
            ready.set()

        with contextlib.suppress(RuntimeError):  # the loop has closed: nobody is left to tell
            _ = loop.call_soon_threadsafe(queue)

    async def _prompt_sent(self, tab_id: str, text: str) -> None:
        await self._in_worker(lambda: self.review.on_prompt(tab_id, text))

    def _sidecar_left(self, connection: Connection) -> None:
        """Not while this core stops: agents outlive a core restart, and the next core restores their tabs."""
        claim = connection.sidecar_claim
        if claim is None or self._closing:
            return
        task = asyncio.create_task(self._sidecar_gone(claim, connection.peer_pid))
        self._notices.add(task)
        task.add_done_callback(self._notices.discard)

    async def _sidecar_gone(self, claim: tuple[str, str], pid: int | None) -> None:
        """A sidecar still running lost only its socket: its terminal and agent are alive, so leave them."""
        await asyncio.sleep(_SIDECAR_GRACE_S)
        if self._closing:
            return
        if pid is not None and pid != os.getpid() and _process_alive(pid):
            _LOG.warning("tab %s: sidecar %d lost its connection but runs on; its agent is left alone", claim[0], pid)
            return
        _LOG.info("tab %s: sidecar is gone", claim[0])
        await self._in_worker(lambda: self.tabs.sidecar_gone(*claim))

    def _agent_notice(self, tab_id: str, text: str) -> None:
        task = asyncio.create_task(self._in_worker(lambda: self.tabs.agent_notice(tab_id, text)))
        self._notices.add(task)
        task.add_done_callback(self._notices.discard)

    def _screen_signal(self, tab_id: str, kind: str, text: str | None) -> None:
        task = asyncio.create_task(self._in_worker(lambda: self.tabs.screen_signal(tab_id, kind, text)))
        self._notices.add(task)
        task.add_done_callback(self._notices.discard)

    def _send_to_tab(self, tab_id: str, message: str) -> CommandResult:
        worker = self._worker
        if worker is None:
            return self.review.send_text(tab_id, message)
        return worker.submit(self.review.send_text, tab_id, message).result()

    def _publish_facts(self, project_id: str, facts: tuple[FactReviewView, ...]) -> str | None:
        """Into the vault on its own thread. The path is known now, and the note follows."""
        lines = tuple(
            f"{item.title}: {', '.join(item.changed_paths)} (since {item.verified_commit[:10]})" for item in facts
        )

        def write(_nothing: None) -> None:
            self.vault.publish_facts(project_id, lines)

        self._to_vault(write, None)
        path = self.vault.facts_note(project_id)
        return None if path is None or not facts else str(path)

    def _node_held_by(self, tab_id: str) -> str | None:
        return self._plans.node_held_by(tab_id)

    def _session_at(self, project_id: str, path: str) -> WorkSession | None:
        primary = self.tabs.work_sessions.at(project_id, path)
        if primary is not None:
            return primary
        for tab in self.tabs.git_tabs(project_id):
            if tab.work_session_id and Path(tab.worktree).resolve() == Path(path).resolve():
                return self.tabs.work_sessions.require(project_id, tab.work_session_id)
        history = self.tabs.history
        if history is not None:
            for record in self.tabs.work_sessions.all(project_id, include_archived=True):
                if any(row.worktree == path for row in history.records(record.id)):
                    return record
        return None

    def _session_actor_context(self, tab_id: str) -> tuple[str, str, str | None]:
        title, agent, node = self.tabs.actor_context(tab_id)
        return title, agent, node or self._plans.node_held_by(tab_id)

    def _close_session_worker(self, tab_id: str) -> None:
        _ = self.tabs.close_tab(CloseTabParams(tab_id=TabId(tab_id)))

    def _session_workspace_removed(self, project_id: str, path: str) -> None:
        if self._worker is None:
            self.tabs.archive_removed_workspace(project_id, path)
        else:
            _ = self._worker.submit(self.tabs.archive_removed_workspace, project_id, path).result()

    def _inspect_session_workspace(self, project_id: str, path: str) -> tuple[str, str | None]:
        _root, row = self.git.state.worktree_row(project_id, path)
        return row.path, row.branch

    def _git_heard(self, project_id: str) -> None:
        self.git.heard(project_id)

    def _facts_to_check(self, project_id: str) -> tuple[StoredFact, ...]:
        return self._plans.facts_to_check(project_id)

    def _after_merge(self, record: GitOperationRecord) -> None:
        if record.source.startswith("refs/aconsole/sessions/"):
            return
        if record.result_oid is not None:
            self._plans.record_merge(node_id=record.plan_node, tab_id=record.tab_id, oid=record.result_oid)
            history = self.tabs.history
            if record.work_session_id is not None and history is not None:
                session = self.tabs.work_sessions.require(record.project_id, record.work_session_id)
                accepted = {session.worktree: float("inf")}
                merges = self.git.merges
                for point in history.records(record.work_session_id):
                    if point.kind == "result" and point.oid and merges is not None and merges.landed_from(
                        record.project_id, point.ref, point.oid,
                    ) is not None:
                        accepted[point.worktree] = max(accepted.get(point.worktree, 0), point.sequence)
                nodes: set[str] = set()
                for point in history.records(record.work_session_id):
                    if point.sequence <= accepted.get(point.worktree, 0) and point.kind != "begin" and (
                        point.plan_node is not None and point.plan_node not in nodes
                    ):
                        nodes.add(point.plan_node)
                        self._plans.record_merge(
                            node_id=point.plan_node, tab_id=point.actor_tab_id, oid=record.result_oid,
                        )

    def _close_for_cleanup(self, tab_id: str) -> None:
        """A tab whose agent exited. The tabs belong to the tab worker, and its worktree goes separately."""
        worker = self._worker
        if worker is None:
            self.tabs.close_for_cleanup(tab_id)
            return
        _ = worker.submit(self.tabs.close_for_cleanup, tab_id).result()

    def _keep_before_change(self, tab_id: str, label: str) -> None:
        """Off the loop. The tab's checkpoint list belongs to the tab worker, so it is written there."""
        worker = self._worker
        if worker is None:
            self.review.keep_before_change(tab_id, label)
            return
        worker.submit(self.review.keep_before_change, tab_id, label).result()

    def _git_changed(self, project_id: str) -> None:
        """A write just finished. The answer should not wait for this, and a failed look changes nothing."""
        try:
            _ = self.git.announce(project_id)
        except (GitCommandError, DomainError) as exc:
            _LOG.warning("git look after a change failed: %s", exc)

    async def _watch_git(self) -> None:
        """Sleep until Git changes, a turn ends, or a failed follow-up is due. One pass at a time."""
        wake = asyncio.Event()
        loop = asyncio.get_running_loop()

        def wake_up() -> None:
            _ = loop.call_soon_threadsafe(wake.set)

        self.git.wake = wake_up
        while True:
            with contextlib.suppress(TimeoutError):
                _ = await asyncio.wait_for(wake.wait(), self.git.retry_in())
            wake.clear()
            while (remaining := self.git.settled_in()) is not None and remaining > 0:  # noqa: ASYNC110 -- writes move this
                await asyncio.sleep(remaining)
            try:
                _ = await asyncio.to_thread(self.git.check)
            except Exception:  # noqa: BLE001 -- logged; the next change brings another pass
                _LOG.exception("git check failed")

    async def _watch_codex_turns(self) -> None:
        """Codex hooks are not trusted (ADR-0002), and notify only says that a turn ended."""
        watch: TurnWatch | None = None
        while True:
            try:
                if watch is None:
                    home = await asyncio.to_thread(self._host.agent_home, _CODEX_CONTAINER)
                    if home is not None:
                        watch = TurnWatch(home / ".codex" / "sessions")
                now = datetime.now(UTC).astimezone()
                changes = () if watch is None else await asyncio.to_thread(watch.changes, now)
                for cwd, working in changes:
                    await self._in_worker(lambda cwd=cwd, working=working: self.tabs.codex_turn(cwd, working=working))
                    await self._note_codex_session(cwd, watch.session_in(cwd) if watch is not None else None)
            except Exception:  # noqa: BLE001 -- logged; the next change brings another pass
                _LOG.exception("cannot read Codex turns")
            await asyncio.sleep(_CODEX_TURN_POLL_S)

    async def _note_codex_session(self, cwd: str, session: str | None) -> None:
        if session is not None:
            await self._in_worker(lambda: self.tabs.codex_session(cwd, session))

    async def _poll_claude_limits(self) -> None:
        """`/usage` is what Claude itself shows. A statusline only repeats its tab's last reply, so it is not used."""
        while True:
            if not await self._connected(AgentKind.CLAUDE):
                await self._limits_wait(self._claude_usage_due, _CODEX_RETRY_S)
                continue
            try:
                text = await asyncio.to_thread(self._host.claude_usage, _CLAUDE_CONTAINER)
                event = await asyncio.to_thread(self._claude_limits.on_usage, text)
                if event is not None:
                    event = await self._with_tokens(event, self._claude_tokens)
                    self._account_observed(event, source="claude-usage", every_s=_CLAUDE_POLL_S)
                elif text.strip():
                    # The card keeps showing when it last heard: the numbers look stale rather than wrong.
                    _LOG.warning("claude /usage gave no limits: %.200s", text)
            except Exception:  # noqa: BLE001 -- logged; the next change brings another pass
                _LOG.exception("cannot read Claude limits")
            await self._limits_wait(self._claude_usage_due, _CLAUDE_POLL_S)

    async def _poll_codex_limits(self) -> None:
        """Codex reports limits only when asked, and answers without any open tab."""
        due = self._limits_due("codex")
        while True:
            if not await self._connected(AgentKind.CODEX):
                await self._limits_wait(due, _CODEX_RETRY_S)
                continue
            try:
                body = await asyncio.to_thread(self._host.codex_rate_limits, _CODEX_CONTAINER)
                event = await self._in_worker(lambda body=body: self._codex_limits.on_rate_limits(_CODEX_POLL, body))
                if event is not None:
                    event = await self._with_tokens(event, self._codex_tokens)
                    self._account_observed(event, source="codex-app-server", every_s=_CODEX_POLL_S)
                elif body:
                    # An error reply, such as a lost login. It says why and holds no credentials.
                    _LOG.warning("Codex gave no limits: %s", body[:_CODEX_REPLY_LOGGED].decode(errors="replace"))
            except Exception:  # noqa: BLE001 -- logged; the next change brings another pass
                _LOG.exception("cannot read Codex limits")
                body = b""
            await self._limits_wait(due, codex_poll_delay(body))

    async def _poll_cursor_limits(self) -> None:
        """Cursor CLI prints no limits; its backend answers them to the CLI's own token."""
        due = self._limits_due("cursor")
        while True:
            if not await self._connected(AgentKind.CURSOR):
                await self._limits_wait(due, _CODEX_RETRY_S)
                continue
            body = b""
            try:
                body, plan, models = await asyncio.to_thread(self._host.cursor_usage, _CURSOR_CONTAINER)
                event = self._cursor_limits.on_usage(body, plan, models)
                if event is not None:
                    self._account_observed(event, source="cursor-dashboard", every_s=_CURSOR_POLL_S)
                elif body:
                    _LOG.warning("Cursor gave no limits: %s", body[:_CODEX_REPLY_LOGGED].decode(errors="replace"))
            except Exception:  # noqa: BLE001 -- logged; the next change brings another pass
                _LOG.exception("cannot read Cursor limits")
            # A stopped container or a missing login gives nothing: ask again soon, as for Codex.
            await self._limits_wait(due, _CURSOR_POLL_S if body else _CODEX_RETRY_S)

    async def _poll_opencode_limits(self) -> None:
        """OpenCode Go counts 5 hours, a week and a month. Without a Go subscription there is nothing to count."""
        due = self._limits_due("opencode")
        said = b""
        while True:
            if not await self._connected(AgentKind.OPENCODE):
                await self._limits_wait(due, _CODEX_RETRY_S)
                continue
            body = b""
            try:
                body = await asyncio.to_thread(self._host.opencode_usage, _OPENCODE_CONTAINER)
                event = self._opencode_limits.on_usage(body)
                if event is not None:
                    self._account_observed(event, source="opencode-go", every_s=_OPENCODE_POLL_S)
                elif body and body != said:
                    # Without a Go subscription the refusal comes back every poll: it is logged once.
                    said = body
                    _LOG.warning("OpenCode gave no limits: %s", body[:_CODEX_REPLY_LOGGED].decode(errors="replace"))
            except Exception:  # noqa: BLE001 -- logged; the next change brings another pass
                _LOG.exception("cannot read OpenCode limits")
            await self._limits_wait(due, _OPENCODE_POLL_S if body else _CODEX_RETRY_S)

    def _log_folder(self, container: str, logs: str) -> Path | None:
        home = self._host.agent_home(container)
        return None if home is None else home / logs

    @staticmethod
    async def _with_tokens(event: QuotaUpdated, log: UsageLog) -> QuotaUpdated:
        """The account with its tokens since the long window began. Without them the limits still count."""
        since = tokens_since(event.account, datetime.now(UTC))
        try:
            models = await asyncio.to_thread(log.models, since)
        except Exception:  # noqa: BLE001 -- logged; the thread keeps serving other plans
            _LOG.exception("cannot count tokens")
            return event
        return QuotaUpdated(account=msgspec.structs.replace(event.account, usage=models, usage_since=since))

    async def _connected(self, kind: AgentKind) -> bool:
        """Asked before each poll. It looks at a file, so an agent logged in later is picked up within a minute."""
        return is_connected(await asyncio.to_thread(self._agents_source), kind)

    def _limits_due(self, name: str) -> asyncio.Event:
        event = asyncio.Event()
        self._limits_events[name] = event
        return event

    @staticmethod
    async def _limits_wait(due: asyncio.Event, delay_s: float) -> None:
        """Until the next poll, or until someone asks for the limits now."""
        due.clear()
        with contextlib.suppress(TimeoutError):
            _ = await asyncio.wait_for(due.wait(), delay_s)

    def refresh_limits(self) -> None:
        """`quota/refresh`: every poll asks now, and what it hears is sent even when the numbers did not move."""
        self._account_sent.clear()
        self._claude_usage_due.set()
        for event in self._limits_events.values():
            event.set()

    def _account_observed(
        self, event: QuotaUpdated, *, source: str | None = None, every_s: float | None = None,
    ) -> None:
        """The snapshot always holds the newest; subscribers hear of a change, or at most twice a minute without one."""
        if source is not None:
            event = QuotaUpdated(
                account=msgspec.structs.replace(
                    event.account, source=source, refresh_s=None if every_s is None else int(every_s),
                ),
            )
        account = event.account
        warn, critical = self._quota_thresholds
        now_at = datetime.now(UTC)
        account = msgspec.structs.replace(account, quota=tuple(msgspec.structs.replace(window, level=QuotaLevel(
            quota_level(used_pct=window.used_pct, resets_at=window.resets_at, now=now_at,
                warn_pct=warn, critical_pct=critical).value,
        )) for window in account.quota))
        event = QuotaUpdated(account=account)
        key = str(account.id)
        previous = self._accounts.get(key)
        now = time.monotonic()
        changed = previous is None or _windows(previous) != _windows(account)
        self._accounts = {**self._accounts, key: account}
        if changed or now - self._account_sent.get(key, 0.0) >= _ACCOUNT_RESEND_S:
            self._account_sent[key] = now
            self._publish(event, self._live_tabs, None)

    async def _close_ingest(self) -> None:
        self._agent_commands.stop_streams()
        server = self._uvicorn
        self._uvicorn = None
        if server is not None:
            server.should_exit = True
        tasks = self._ingest_tasks
        self._ingest_tasks = []
        for task in tasks[1:]:
            _ = task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _restore_tabs(self) -> None:
        """Load settings and open tabs before the socket accepts anyone.

        `tab_added` is published here, so the first `events/subscribe` replays tabs that already exist.
        Restored tabs carry no token; `tab/launchSpec` mints the next one.
        """
        writer = self._writer
        if writer is None:
            return
        store = TabDatabase(writer)
        checkpoints = CheckpointDatabase(writer)
        self.review.checkpoints.use_journal(checkpoints)
        self.review.checkpoints.protected_ref = checkpoints.is_session_ref
        await asyncio.to_thread(self._plans.use_store, PlanDatabase(writer))
        restored_plans = await asyncio.to_thread(self._plans.views)
        with self._palette_plan_lock:
            self._palette_plans.update((str(plan.id), plan) for plan in restored_plans)
        await asyncio.to_thread(self._plans.use_knowledge, KnowledgeDatabase(writer))
        await asyncio.to_thread(self.vault.use_bases, VaultFileDatabase(writer))
        self.tabs.use_store(store)
        self.tabs.use_projects(self._catalog, store)
        policy = ApprovalPolicyBook()
        await asyncio.to_thread(policy.attach, store)
        self.tabs.use_policy(policy)
        self._approval_policy = policy
        await asyncio.to_thread(self.tabs.work_sessions.use_store, WorkSessionDatabase(writer))
        if self.tabs.history is not None:
            await asyncio.to_thread(self.tabs.history.use_store, WorkSessionHistoryDatabase(writer))
            for project in self._catalog.views():
                for record in self.tabs.work_sessions.all(str(project.id), include_archived=True):
                    try:
                        await asyncio.to_thread(self.tabs.history.recover, record, Path(project.root))
                    except GitCommandError as exc:
                        _LOG.warning("session %s history is unavailable: %s", record.id, exc)
        found = await asyncio.to_thread(store.settings)
        self.tabs.note_settings(found.rev, setup_completed=found.setup_completed)
        if found.worktrees_dir is not None:
            self._host.set_worktrees_dir(Path(found.worktrees_dir))
        caught: list[UiEvent] = []
        previous = self.tabs.listener
        self.tabs.listener = caught.append
        try:
            await self._in_worker(self.tabs.reload)
        finally:
            self.tabs.listener = previous
        live = self.tabs.running_count()
        for event in caught:
            self._publish(event, live, None)
        self.tabs.on_settings_changed = self._use_vault
        self._use_vault(await self._in_worker(self.tabs.current_settings))
        merges = self.git.merges
        if merges is not None:
            await asyncio.to_thread(merges.use_journal, GitOperationDatabase(writer))

    async def _open_database(self) -> None:
        """DATA_MODEL §5: backup and upgrade before the pool opens, then the writer and the sweep loop."""
        path = self.database
        if path is None:
            return
        _ = await asyncio.to_thread(prepare_database, path, now=datetime.now(UTC))
        engine = write_engine(sqlite_url(path))
        writer = Writer(engine)
        writer.start()
        self._engine = engine
        self._writer = writer
        self._sweeper = asyncio.create_task(self._sweep_loop(writer))
        self._health = asyncio.create_task(self._health_loop())

    def _palette_snapshot(self) -> NavigationSnapshot:
        with self._palette_plan_lock:
            plans = tuple(self._palette_plans.values())
        return msgspec.structs.replace(
            self.tabs.navigation_snapshot(), plans=plans,
            approvals=self.approvals.pending_views(), vault_ok=self.vault.root is not None,
        )

    def _palette_note_uri(self, note: str) -> str:
        path = self.vault.resolve(note)
        if path is None:
            message = "note is no longer available"
            raise ValueError(message)
        return path.as_uri()

    def health(self) -> HealthResult:
        """What a slow or failing core looks like from outside: the writer's backlog, its failures, and IPC times."""
        writer = self._writer
        return HealthResult(
            database=writer is not None,
            writer_queued=0 if writer is None else writer.queued,
            writer_failed=0 if writer is None else writer.failed_jobs,
            writer_last_commit_ms=0.0 if writer is None else writer.last_tick_s * _MS_PER_S,
            ipc_answered=self._ipc.answered,
            ipc_slowest_ms=self._ipc.recent_slowest_s * _MS_PER_S,
        )

    async def _health_loop(self) -> None:
        while True:
            await asyncio.sleep(_HEALTH_EVERY_S)
            if self._ipc.in_window > 0:
                _LOG.info("health: %s", self.health())
            self._ipc.next_window()

    async def _sweep_loop(self, writer: Writer) -> None:
        while True:
            try:
                # One portion per commit, so that writes queued meanwhile get their turn.
                more = True
                while more:
                    swept = await writer.submit(self._sweep_once)
                    more = isinstance(swept, SweepResult) and swept.more
                await self._drop_swept_refs()
                if self._engine is not None:
                    status = await asyncio.to_thread(checkpoint_wal, self._engine, truncate=True)
                    if status.busy:
                        _LOG.warning("WAL checkpoint deferred while a reader is active")
            except Exception:  # noqa: BLE001 -- logged; the next change brings another pass
                _LOG.exception("retention sweep failed")
            await asyncio.sleep(self.sweep_every_s)

    async def _drop_swept_refs(self) -> None:
        """Outbox survives a restart even after the owning tab or workspace disappears."""
        writer = self._writer
        if writer is None:
            return
        refs = await self._in_worker(lambda: drain_ref_deletions(RefDeletionQueue(writer)))
        if refs:
            _ = await self._in_worker(lambda: self.review.forget_refs(refs))
        self.dropped_refs.clear()

    def _sweep_once(self, session: Session) -> object:
        return sweep(session, now=datetime.now(UTC), drop_ref=self.dropped_refs.append)

    async def _close_database(self) -> None:
        sweeper = self._sweeper
        self._sweeper = None
        if sweeper is not None:
            _ = sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
        health = self._health
        self._health = None
        if health is not None:
            _ = health.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await health
        writer = self._writer
        self._writer = None
        if writer is not None:
            while True:
                try:
                    await asyncio.to_thread(writer.stop)
                    break
                except TimeoutError:
                    _LOG.warning("waiting for the database writer before releasing the core lock")
        engine = self._engine
        self._engine = None
        if engine is not None:
            engine.dispose()

    def _quiet(self) -> bool:
        return self._holders == 0 and self._live_tabs == 0

    def _on_presence(self, *, was_quiet: bool) -> None:
        if self._closing:
            return
        quiet = self._quiet()
        if quiet and not was_quiet:
            self._arm_idle()
        elif was_quiet and not quiet:
            self._cancel_idle()

    def _gain_holder(self) -> None:
        was_quiet = self._quiet()
        self._holders += 1
        self._on_presence(was_quiet=was_quiet)

    def _lose_holder(self) -> None:
        was_quiet = self._quiet()
        if self._holders > 0:
            self._holders -= 1
        self._on_presence(was_quiet=was_quiet)

    def _arm_idle(self) -> None:
        # IPC §2: 15s after the last ui/cli client, and only when no tab is live.
        self._cancel_idle()
        self._idle = asyncio.create_task(self._idle_fire())

    def _cancel_idle(self) -> None:
        task = self._idle
        self._idle = None
        if task is None or task.done():
            return
        _ = task.cancel()
        task.add_done_callback(self._ignore_cancel)

    @staticmethod
    def _ignore_cancel(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        _ = task.exception()

    async def _idle_fire(self) -> None:
        try:
            await asyncio.sleep(self.idle_s)
        except asyncio.CancelledError:
            return
        if self._idle is asyncio.current_task():
            self._idle = None
        if self._quiet() and not self._closing:
            self.request_stop()

    async def _listen(self) -> None:
        directory = self.socket.parent
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(_DIR_MODE)
        if self.socket.exists():
            self.socket.unlink()
        self._server = await asyncio.start_unix_server(
            self._client,
            self.socket,
            limit=self._frame_limit + 1,
        )
        self.socket.chmod(_FILE_MODE)

    async def _close_server(self) -> None:
        for writer in tuple(self._writers):
            if not writer.is_closing():
                writer.close()
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        if self.socket.exists():
            self.socket.unlink()

    async def _answer(self, connection: Connection, writer: asyncio.StreamWriter, frame: bytes) -> bool:
        """Handle one frame on the worker and write the replies. False when the frame cannot be decoded."""
        started = time.perf_counter()
        envelope: Envelope | None = None
        try:
            envelope = decode_envelope(frame)
            if _route(envelope, ready=connection.ready) is _Route.NOW:
                payloads = connection.receive(frame)
            elif isinstance(envelope, Request) and envelope.method == _IMPACT:
                # Building a graph can take a minute; the tab worker must keep answering.
                async with self._impacts:
                    payloads = await asyncio.to_thread(connection.receive, frame)
            elif isinstance(envelope, Request) and envelope.method.startswith(_GIT_PREFIX):
                async with self._git_slots:
                    payloads = await asyncio.to_thread(connection.receive, frame)
            else:
                payloads = await self._in_worker(lambda: connection.receive(frame))
        except msgspec.DecodeError:
            payloads = connection.receive(frame)
        self._ipc.note(time.perf_counter() - started)
        for payload in payloads:
            writer.write(self._within_limit(payload, envelope))
            await writer.drain()
        connection.drained()
        return True

    def _within_limit(self, payload: bytes, envelope: Envelope | None) -> bytes:
        """The reply itself, or a typed refusal when the client could not read it."""
        if len(payload) <= self._frame_limit + 1:
            return payload
        request_id = envelope.id if isinstance(envelope, Request) else None
        _LOG.warning("reply to %s is %d bytes, over the limit", getattr(envelope, "method", "?"), len(payload))
        return _too_large(self._encoder, self._frame_limit, request_id)

    async def _admit(
        self,
        connection: Connection,
        writer: asyncio.StreamWriter,
        frame: bytes,
        turns: asyncio.Queue[bytes | None],
        apart: set[asyncio.Task[None]],
    ) -> None:
        """Start one frame without waiting for the work before it, unless it must follow that work."""
        try:
            envelope = decode_envelope(frame)
        except msgspec.DecodeError:
            await turns.put(frame)  # Its turn returns PARSE_ERROR without discarding later valid frames.
            return
        route = _route(envelope, ready=connection.ready)
        if route is _Route.NOW:
            _ = await self._answer(connection, writer, frame)
            return
        request_id = envelope.id if isinstance(envelope, Request) else None
        # Register accepted queued work before it can receive a timeout cancellation.
        apart_ready = ((route is _Route.APART and len(apart) < _MAX_APART)
                       or (route is _Route.GIT and len(apart) < _MAX_QUEUED))
        turn_ready = route is _Route.TURN and not turns.full()
        if (connection.ready and (apart_ready or turn_ready)
                and request_id is not None and not connection.admit(request_id)):
            writer.write(connection.busy(request_id))
            await writer.drain()
            return
        if apart_ready:
            task = asyncio.create_task(self._answer_apart(connection, writer, frame))
            apart.add(task)
            task.add_done_callback(apart.discard)
            return
        if turn_ready:
            turns.put_nowait(frame)
            return
        if request_id is None:
            await turns.put(frame)
            return
        writer.write(connection.busy(request_id))
        await writer.drain()

    async def _answer_apart(self, connection: Connection, writer: asyncio.StreamWriter, frame: bytes) -> None:
        try:
            _ = await self._answer(connection, writer, frame)
        except (ConnectionError, OSError) as exc:
            _LOG.debug("client left before its answer: %s", exc)

    async def _take_turns(
        self,
        connection: Connection,
        writer: asyncio.StreamWriter,
        turns: asyncio.Queue[bytes | None],
    ) -> None:
        """Run a connection's ordinary frames one by one, in the order they came."""
        while (frame := await turns.get()) is not None:
            was_ready = connection.ready
            role = initialize_role(frame)
            try:
                if not await self._answer(connection, writer, frame):
                    writer.close()
                    continue
            except (ConnectionError, OSError) as exc:
                _LOG.debug("client left before its answer: %s", exc)
            if not connection.holds_core and not was_ready and role in _HOLDS and connection.ready:
                connection.holds_core = True
                self._gain_holder()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        raw = writer.get_extra_info("socket")  # pyright: ignore[reportAny] -- asyncio types this as Any
        pid, uid = credentials(raw)  # pyright: ignore[reportAny] -- asyncio types this as Any
        if not allow_peer(uid):
            writer.close()
            await writer.wait_closed()
            return
        connection = Connection(
            self._ring, tabs=self.tabs, approval_hold=self.approvals, setup_service=self.setup_service,
            review=self.review, knowledge=self.knowledge, git=self.git,
            host_namespace=same_mnt_namespace(pid), palette=self.palette, peer_pid=pid, frame_limit=self._frame_limit,
        )
        connection.on_agent_notice = self._agent_notice
        connection.on_screen_signal = self._screen_signal
        connection.health = self.health
        connection.quota_refresh = self.refresh_limits
        self._writers.add(writer)
        self._connections[connection] = writer
        turns: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=_MAX_QUEUED)
        runner = asyncio.create_task(self._take_turns(connection, writer, turns))
        apart: set[asyncio.Task[None]] = set()
        try:
            while True:
                try:
                    frame = await read_frame(reader, limit=self._frame_limit)
                except MessageTooLargeError as exc:
                    writer.write(_too_large(self._encoder, self._frame_limit))
                    await writer.drain()
                    if not exc.frame_consumed and not await discard_frame(reader, limit=self._frame_limit):
                        break
                    continue
                if frame is None:
                    break
                await self._admit(connection, writer, frame, turns, apart)
        except (ConnectionError, OSError) as exc:
            _LOG.debug("client connection failed: %s", exc)
        finally:
            # What was already read still runs: a sidecar reports its exit and closes straight after.
            await turns.put(None)
            _ = await asyncio.gather(runner, *apart, return_exceptions=True)
            self._writers.discard(writer)
            _ = self._connections.pop(connection, None)
            self._sidecar_left(connection)
            if (connection.ready and connection.role is ClientRole.UI and
                "approvals.inline" in connection.capabilities and not self._has_approval_ui()):
                self.approvals.release_pending()
            if connection.holds_core:
                self._lose_holder()
            if not writer.is_closing():
                writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()


async def run_with_signals(daemon: Daemon) -> int:
    """Run until idle shutdown or SIGTERM/SIGINT. Says so when the lock is taken."""
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, daemon.request_stop)
    loop.add_signal_handler(signal.SIGINT, daemon.request_stop)
    try:
        code = await daemon.serve()
    finally:
        _ = loop.remove_signal_handler(signal.SIGTERM)
        _ = loop.remove_signal_handler(signal.SIGINT)
    if code == EXIT_REFUSED:
        _ = sys.stderr.write("core lock is held\n")
    return code
