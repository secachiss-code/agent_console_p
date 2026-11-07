"""One IPC connection: handshake, inflight cap, cancel, and backpressure."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import final

import msgspec

from aconsole.contracts.app import (
    ApplyFixParams,
    DecideParams,
    EmptyParams,
    EmptyResult,
    FixIdParams,
    HealthResult,
    QuitCheckResult,
    ReleaseParams,
    ScreenTextReplyParams,
    SnapshotResult,
    SubscribeParams,
    SubscribeResult,
)
from aconsole.contracts.common import ClientRole
from aconsole.contracts.errors import (
    ErrorData,
    ErrorObject,
    ProtocolVersionUnsupportedDetails,
    error_by_name,
)
from aconsole.contracts.events import ApprovalRequested, ApprovalResolved, ResyncRequired, UiEvent
from aconsole.contracts.ipc import (
    CancelParams,
    EventEnvelope,
    InitializeParams,
    InitializeResult,
    Notification,
    ProtocolLimits,
    ProtocolVersion,
    Request,
    ResponseErr,
    ResponseOk,
    SidecarSignal,
)
from aconsole.core.application.ports.git_repo import GitCommandError
from aconsole.core.application.services.approval_broker import ApprovalHold
from aconsole.core.application.services.git import GIT_METHODS, GitService
from aconsole.core.application.services.palette import PALETTE_METHODS, PaletteService
from aconsole.core.application.services.review import REVIEW_METHODS, ReviewService
from aconsole.core.application.services.setup import SetupService
from aconsole.core.application.services.tabs import SETTINGS_METHODS, TAB_METHODS, TabRequestError, TabService
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.ipc.framing import decode_envelope, encode_line
from aconsole.core.infrastructure.ipc.ring import EventRing
from aconsole.core.infrastructure.knowledge import KNOWLEDGE_METHODS, KnowledgeService

_LOG = logging.getLogger(__name__)
_SERVER = ProtocolVersion(major=1, minor=0)
# Only what this process serves.
_SERVER_CAPS = frozenset(
    {
        "events.v1",
        "approvals.inline",
        "notifications.desktop",
        "diff.v1",
        "checkpoints.v1",
        "git.v1",
        "work_sessions.v1",
        "terminal.inject",
    },
)
_HOLD = "core/hold"
_SNAPSHOT = "app/snapshot"
_SIDECAR_PREFIX = "sidecar/"
_SIDECAR_SIGNAL = "sidecar/signal"
_SCREEN_SIGNALS = frozenset({"input_submitted", "screen_quiet", "screen_active"})
_APP_METHODS = frozenset({"app/quitCheck", "app/stopAgents", "terminal/screenTextReply"})
OUTBOUND_LIMIT = 1024


def _server_version() -> str:
    from aconsole import __version__  # noqa: PLC0415 -- lightweight package version

    return __version__


@final
class Connection:
    """Protocol state for a single accepted socket."""

    def __init__(  # noqa: PLR0913 -- connection limits and service injection
        self,
        ring: EventRing,
        *,
        outbound_limit: int = OUTBOUND_LIMIT,
        session_id: str = "ipc-session",
        tabs: TabService | None = None,
        approval_hold: ApprovalHold | None = None,
        setup_service: SetupService | None = None,
        review: ReviewService | None = None,
        knowledge: KnowledgeService | None = None,
        git: GitService | None = None,
        host_namespace: bool = False,
        palette: PaletteService | None = None,
    ) -> None:
        self.ring = ring
        self.outbound_limit = outbound_limit
        self.session_id = session_id
        self.tabs = tabs
        self.approval_hold = approval_hold
        self.setup_service = setup_service
        self.review = review
        self.knowledge = knowledge
        self.git = git
        self.palette = palette
        self.capabilities: frozenset[str] = frozenset()
        self.ready = False
        self.paused = False
        self.subscribed = False
        self.role: ClientRole | None = None
        self.on_agent_notice: Callable[[str, str], None] | None = None
        self.on_screen_signal: Callable[[str, str, str | None], None] | None = None
        self.holds_core = False
        self.health: Callable[[], HealthResult] | None = None
        # `quota/refresh`: wake every limits poll now. Called on the loop; it only sets events.
        self.quota_refresh: Callable[[], None] | None = None
        self._sidecar_tab: str | None = None
        self.sidecar_claim: tuple[str, str] | None = None
        self.peer_pid: int | None = None
        # UI and CLI are accepted only from the core's mount namespace. A container stays a sidecar.
        self.host_namespace = host_namespace
        self.inflight: set[int] = set()
        self._queued = 0
        self._encoder = msgspec.json.Encoder()
        self._limits = ProtocolLimits()

    def receive(self, line: bytes) -> tuple[bytes, ...]:
        try:
            envelope = decode_envelope(line)
        except msgspec.DecodeError:
            return (self._err(None, "PARSE_ERROR"),)
        if isinstance(envelope, Notification):
            return self._notification(envelope)
        if not isinstance(envelope, Request):
            return (self._err(None, "INVALID_REQUEST"),)
        return self._request(envelope)

    def push_event(self, event: UiEvent, at: datetime) -> tuple[bytes, ...]:
        """Number the event and queue it, or send one backpressure resync."""
        return self.deliver(self.ring.append(event, at))

    def deliver(self, item: EventEnvelope) -> tuple[bytes, ...]:
        """Frames for an event the ring already numbered. Nothing before `events/subscribe` or while paused."""
        if not self.ready or self.paused or not self.subscribed:
            return ()
        if (
            isinstance(item.event, (ApprovalRequested, ApprovalResolved))
            and "approvals.inline" not in self.capabilities
        ):
            return ()
        if self._queued >= self.outbound_limit:
            self.paused = True
            self._queued = 0
            resync = EventEnvelope(seq=item.seq, at=item.at, event=ResyncRequired(reason="backpressure"))
            return (self._event(resync),)
        self._queued += 1
        return (self._event(item),)

    def busy(self, request_id: int) -> bytes:
        """The refusal for a request the daemon has no room to run."""
        return self._err(request_id, "SERVER_BUSY")

    def drained(self) -> None:
        """The transport wrote out everything handed to it: queued events no longer count toward the limit."""
        self._queued = 0

    def _request(self, request: Request) -> tuple[bytes, ...]:
        if request.method == "initialize":
            return self._initialize(request)
        if not self.ready:
            return (self._err(request.id, "NOT_INITIALIZED"),)
        return self._ready(request)

    def _ready(self, request: Request) -> tuple[bytes, ...]:  # noqa: C901, PLR0911, PLR0912 -- protocol dispatch
        if request.method == "shutdown":
            return (self._ok(request.id, EmptyResult()),)
        # APP_API §9: a sidecar may call only `sidecar/*`; ui and cli may not call those.
        if (self.role is ClientRole.SIDECAR) != request.method.startswith(_SIDECAR_PREFIX):
            return (self._err(request.id, "ROLE_FORBIDDEN"),)
        if request.method == "terminal/prepareImagePaste" and self.role is not ClientRole.UI:
            return (self._err(request.id, "ROLE_FORBIDDEN"),)
        if request.method == "events/subscribe":
            self.paused = False
            self._queued = 0
            return self._subscribe(request)
        if request.method == "events/unsubscribe":
            self.subscribed = False
            return (self._ok(request.id, EmptyResult()),)
        if len(self.inflight) >= self._limits.max_inflight_requests:
            return (self._err(request.id, "SERVER_BUSY"),)
        if request.method == _HOLD:
            self.inflight.add(request.id)
            return ()
        if request.method == "app/health" and self.health is not None:
            return (self._ok(request.id, self.health()),)
        if request.method == "quota/refresh" and self.quota_refresh is not None:
            self.quota_refresh()
            return (self._ok(request.id, EmptyResult()),)
        if request.method == "approval/decide" and self.approval_hold is not None:
            return self._decide_approval(request, self.approval_hold)
        if request.method == "approval/releaseToNative" and self.approval_hold is not None:
            return self._release_to_native(request, self.approval_hold)
        if request.method.startswith("setup/") and self.setup_service is not None:
            return self._setup(request, self.setup_service)
        if request.method in _APP_METHODS and self.tabs is not None:
            return self._app(request, self.tabs)
        if request.method in PALETTE_METHODS and self.palette is not None:
            return self._service(request, self.palette.dispatch)
        if request.method in REVIEW_METHODS and self.review is not None:
            return self._service(request, self.review.dispatch)
        if request.method in GIT_METHODS and self.git is not None:
            return self._service(request, self.git.dispatch)
        if request.method in KNOWLEDGE_METHODS and self.knowledge is not None:
            return self._service(request, self.knowledge.dispatch)
        served = TAB_METHODS | SETTINGS_METHODS
        if request.method == _SNAPSHOT or (self.tabs is not None and request.method in served):
            return self._tab(request)
        return (self._err(request.id, "METHOD_NOT_FOUND"),)

    def _service(  # noqa: PLR0911 -- one answer per kind of refusal
        self, request: Request, dispatch: Callable[[str, bytes], msgspec.Struct],
    ) -> tuple[bytes, ...]:
        try:
            result = dispatch(request.method, bytes(request.params))
        except msgspec.DecodeError:
            return (self._err(request.id, "INVALID_PARAMS"),)
        except TabRequestError as exc:
            return (self._err(request.id, exc.name),)
        except DomainError as exc:
            return (self._err(request.id, exc.name, exc.extra),)
        except GitCommandError as exc:
            _LOG.warning("%s: %s: %s", request.method, exc, exc.detail)
            return (self._err(request.id, "GIT_COMMAND_FAILED"),)
        except ValueError:
            return (self._err(request.id, "INVALID_PARAMS"),)
        except Exception:  # noqa: BLE001 -- answer the request; the connection serves the next one
            # A git failure must answer the request; the connection stays up for the next one.
            _LOG.exception("%s failed", request.method)
            return (self._err(request.id, "INTERNAL_ERROR"),)
        return (self._ok(request.id, result),)

    def _app(self, request: Request, tabs: TabService) -> tuple[bytes, ...]:
        try:
            if request.method == "terminal/screenTextReply":
                # The core asks for screen text only in `/switch`, which has no caller yet.
                _ = msgspec.json.decode(request.params, type=ScreenTextReplyParams)
                return (self._ok(request.id, EmptyResult()),)
            _ = msgspec.json.decode(request.params, type=EmptyParams)
        except msgspec.DecodeError:
            return (self._err(request.id, "INVALID_PARAMS"),)
        if request.method == "app/stopAgents":
            tabs.stop_agents()
            return (self._ok(request.id, EmptyResult()),)
        pending = len(self.approval_hold.pending_views()) if self.approval_hold is not None else 0
        return (self._ok(request.id, QuitCheckResult(busy_tabs=tabs.busy_tabs(), pending_approvals=pending)),)

    def _setup(self, request: Request, service: SetupService) -> tuple[bytes, ...]:
        try:
            if request.method == "setup/checks":
                _ = msgspec.json.decode(request.params, type=EmptyParams)
                checks = service.checks()
                if self.tabs is not None:
                    self.tabs.note_setup_checks(
                        sum(not item.ok and item.severity == "error" for item in checks.checks),
                        sum(not item.ok and item.severity == "warning" for item in checks.checks),
                    )
                return (self._ok(request.id, checks),)
            if request.method == "setup/previewFix":
                preview = msgspec.json.decode(request.params, type=FixIdParams)
                return (self._ok(request.id, service.preview_fix(preview.fix_id, at=datetime.now(UTC))),)
            if request.method == "setup/applyFix":
                apply = msgspec.json.decode(request.params, type=ApplyFixParams)
                result = service.apply_fix(apply.fix_id, apply.confirm_token, at=datetime.now(UTC))
                return (self._ok(request.id, result),)
        except msgspec.DecodeError:
            return (self._err(request.id, "INVALID_PARAMS"),)
        except DomainError as exc:
            return (self._err(request.id, exc.name),)
        return (self._err(request.id, "METHOD_NOT_FOUND"),)

    def _decide_approval(self, request: Request, hold: ApprovalHold) -> tuple[bytes, ...]:
        try:
            params = msgspec.json.decode(request.params, type=DecideParams)
            result = hold.decide(params, at=datetime.now(UTC))
        except msgspec.DecodeError:
            return (self._err(request.id, "INVALID_PARAMS"),)
        except DomainError as exc:
            return (self._err(request.id, exc.name),)
        except ValueError:
            return (self._err(request.id, "INVALID_PARAMS"),)
        return (self._ok(request.id, result),)

    def _release_to_native(self, request: Request, hold: ApprovalHold) -> tuple[bytes, ...]:
        try:
            params = msgspec.json.decode(request.params, type=ReleaseParams)
            released = hold.release_to_native(params, at=datetime.now(UTC))
        except msgspec.DecodeError:
            return (self._err(request.id, "INVALID_PARAMS"),)
        except DomainError as exc:
            return (self._err(request.id, exc.name),)
        return (self._ok(request.id, released),)

    def _tab(self, request: Request) -> tuple[bytes, ...]:  # noqa: PLR0911 -- typed refusals keep the connection alive
        tabs = self.tabs
        if tabs is None:
            return (self._err(request.id, "METHOD_NOT_FOUND"),)
        if request.method == _SNAPSHOT:
            return self._snapshot(request, tabs)
        try:
            result = tabs.dispatch(request.method, bytes(request.params))
        except TabRequestError as exc:
            return (self._err(request.id, exc.name),)
        except msgspec.DecodeError:
            return (self._err(request.id, "INVALID_PARAMS"),)
        except DomainError as exc:
            return (self._err(request.id, exc.name, exc.extra),)
        except ValueError:
            return (self._err(request.id, "INVALID_PARAMS"),)
        except Exception:  # noqa: BLE001 -- answer the request; the connection serves the next one
            _LOG.exception("%s failed", request.method)
            return (self._err(request.id, "INTERNAL_ERROR"),)
        return (self._ok(request.id, result),)

    def _snapshot(self, request: Request, tabs: TabService) -> tuple[bytes, ...]:
        try:
            _ = msgspec.json.decode(request.params, type=EmptyParams)
        except msgspec.DecodeError:
            return (self._err(request.id, "INVALID_PARAMS"),)
        snapshot = tabs.snapshot()
        if self.approval_hold is not None and "approvals.inline" in self.capabilities:
            snapshot = msgspec.structs.replace(snapshot, approvals=self.approval_hold.pending_views())
        if self.knowledge is not None:
            snapshot = msgspec.structs.replace(
                snapshot, plans=self.knowledge.board.views(), vault_ok=self.knowledge.vault_ok(),
            )
        result = SnapshotResult(snapshot=snapshot, seq=self.ring.current_seq)
        return (self._ok(request.id, result),)

    def _notification(self, note: Notification) -> tuple[bytes, ...]:
        if note.method.startswith(_SIDECAR_PREFIX):
            # Terminal telemetry. Only an authenticated sidecar is heard; the rest is dropped.
            self._terminal_signal(note)
            return ()
        if note.method != "$/cancelRequest":
            return ()
        try:
            params = msgspec.json.decode(note.params, type=CancelParams)
        except msgspec.DecodeError:
            return ()
        if params.id not in self.inflight:
            return ()
        self.inflight.discard(params.id)
        return (self._err(params.id, "REQUEST_CANCELLED"),)

    def _terminal_signal(self, note: Notification) -> None:
        heard = self.on_agent_notice
        if note.method != _SIDECAR_SIGNAL or self._sidecar_tab is None or heard is None:
            return
        try:
            signal = msgspec.json.decode(note.params, type=SidecarSignal)
        except msgspec.DecodeError:
            return
        if signal.kind == "osc9_notify" and signal.text:
            heard(self._sidecar_tab, signal.text)
        elif signal.kind in _SCREEN_SIGNALS and self.on_screen_signal is not None:
            self.on_screen_signal(self._sidecar_tab, signal.kind, signal.text)

    def _initialize(self, request: Request) -> tuple[bytes, ...]:
        if self.ready:
            return (self._err(request.id, "ALREADY_INITIALIZED"),)
        try:
            params = msgspec.json.decode(request.params, type=InitializeParams)
        except msgspec.DecodeError:
            return (self._err(request.id, "INVALID_PARAMS"),)
        chosen = _negotiate(params.protocol[0], params.protocol[1])
        if chosen is None:
            return (self._err(request.id, "PROTOCOL_VERSION_UNSUPPORTED"),)
        if params.role is not ClientRole.SIDECAR and not self.host_namespace:
            _LOG.warning("refused %s initialize from outside the core mount namespace", params.role)
            return (self._err(request.id, "ROLE_FORBIDDEN"),)
        if params.role is ClientRole.SIDECAR and not self._sidecar_ok(params):
            return (self._err(request.id, "SIDECAR_TOKEN_INVALID"),)
        self.ready = True
        self.role = params.role
        self.capabilities = _SERVER_CAPS & params.capabilities
        result = InitializeResult(
            protocol=chosen,
            server_version=_server_version(),
            server_pid=os.getpid(),
            session_id=self.session_id,
            capabilities=self.capabilities,
            limits=self._limits,
        )
        return (self._ok(request.id, result),)

    def _sidecar_ok(self, params: InitializeParams) -> bool:
        """The one-shot token from `tab/launchSpec`, for that tab, used once."""
        tabs = self.tabs
        if tabs is None or params.tab_id is None or params.sidecar_token is None:
            return False
        claimed = tabs.claim_sidecar(str(params.tab_id), params.sidecar_token)
        if claimed:
            self._sidecar_tab = str(params.tab_id)
            self.sidecar_claim = (str(params.tab_id), params.sidecar_token)
            self.peer_pid = params.pid
        return claimed

    def _subscribe(self, request: Request) -> tuple[bytes, ...]:
        try:
            params = msgspec.json.decode(request.params, type=SubscribeParams)
        except msgspec.DecodeError:
            return (self._err(request.id, "INVALID_PARAMS"),)
        replay = self.ring.since(params.after_seq)
        if replay is None:
            return (self._err(request.id, "RESYNC_REQUIRED"),)
        self.subscribed = True
        frames = [self._ok(request.id, SubscribeResult(current_seq=self.ring.current_seq))]
        frames.extend(self._event(item) for item in replay if self._visible(item))
        self._queued += len(replay)
        return tuple(frames)

    def _visible(self, item: EventEnvelope) -> bool:
        if isinstance(item.event, (ApprovalRequested, ApprovalResolved)):
            return "approvals.inline" in self.capabilities
        return True

    def _ok(self, request_id: int, result: msgspec.Struct) -> bytes:
        body = ResponseOk(id=request_id, result=msgspec.Raw(self._encoder.encode(result)))
        return encode_line(self._encoder, body)

    def _err(self, request_id: int | None, name: str, extra: tuple[str, ...] = ()) -> bytes:
        spec = error_by_name(name)
        details = None
        if name == "PROTOCOL_VERSION_UNSUPPORTED":
            details = ProtocolVersionUnsupportedDetails(supported=("1.0",))
        error = ErrorObject(
            code=spec.code,
            message=spec.name,
            data=ErrorData(
                reason=spec.name,
                domain="aconsole.ipc",
                retryable=spec.retryable,
                user_message_key=f"error.{spec.name.lower()}",
                user_message_args={"reason": extra[0]} if extra else {},
                details=details,
            ),
        )
        return encode_line(self._encoder, ResponseErr(id=request_id, error=error))

    def _event(self, item: EventEnvelope) -> bytes:
        note = Notification(method="$/event", params=msgspec.Raw(self._encoder.encode(item)))
        return encode_line(self._encoder, note)


def _negotiate(low: ProtocolVersion, high: ProtocolVersion) -> ProtocolVersion | None:
    server = (_SERVER.major, _SERVER.minor)
    if (low.major, low.minor) > (high.major, high.minor):
        return None
    if (low.major, low.minor) <= server <= (high.major, high.minor):
        return _SERVER
    return None
