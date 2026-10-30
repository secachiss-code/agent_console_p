"""One IPC connection: handshake, inflight cap, cancel, and backpressure."""

from __future__ import annotations

from datetime import datetime
from typing import final

import msgspec

from aconsole.contracts.app import EmptyParams, SnapshotResult, SubscribeParams, SubscribeResult
from aconsole.contracts.common import ClientRole
from aconsole.contracts.errors import (
    ErrorData,
    ErrorObject,
    ProtocolVersionUnsupportedDetails,
    error_by_name,
)
from aconsole.contracts.events import ResyncRequired, UiEvent
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
)
from aconsole.core.application.services.tabs import SETTINGS_METHODS, TAB_METHODS, TabRequestError, TabService
from aconsole.core.infrastructure.ipc.framing import decode_envelope, encode_line
from aconsole.core.infrastructure.ipc.ring import EventRing

_SERVER = ProtocolVersion(major=1, minor=0)
_SERVER_CAPS = frozenset({"events.v1", "diff.v1", "checkpoints.v1"})
_HOLD = "core/hold"
_SNAPSHOT = "app/snapshot"
_SIDECAR_PREFIX = "sidecar/"
OUTBOUND_LIMIT = 1024


@final
class Connection:
    """Protocol state for a single accepted socket."""

    def __init__(
        self,
        ring: EventRing,
        *,
        outbound_limit: int = OUTBOUND_LIMIT,
        session_id: str = "ipc-session",
        tabs: TabService | None = None,
    ) -> None:
        self.ring = ring
        self.outbound_limit = outbound_limit
        self.session_id = session_id
        self.tabs = tabs
        self.ready = False
        self.paused = False
        self.subscribed = False
        self.role: ClientRole | None = None
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
        if self._queued >= self.outbound_limit:
            self.paused = True
            self._queued = 0
            resync = EventEnvelope(seq=item.seq, at=item.at, event=ResyncRequired(reason="backpressure"))
            return (self._event(resync),)
        self._queued += 1
        return (self._event(item),)

    def drained(self) -> None:
        """The transport wrote out everything handed to it: queued events no longer count toward the limit."""
        self._queued = 0

    def _request(self, request: Request) -> tuple[bytes, ...]:
        if request.method == "initialize":
            return self._initialize(request)
        if not self.ready:
            return (self._err(request.id, "NOT_INITIALIZED"),)
        return self._ready(request)

    def _ready(self, request: Request) -> tuple[bytes, ...]:
        # APP_API §9: a sidecar may call only `sidecar/*`; ui and cli may not call those.
        if (self.role is ClientRole.SIDECAR) != request.method.startswith(_SIDECAR_PREFIX):
            return (self._err(request.id, "ROLE_FORBIDDEN"),)
        if request.method == "events/subscribe":
            self.paused = False
            self._queued = 0
            return self._subscribe(request)
        if len(self.inflight) >= self._limits.max_inflight_requests:
            return (self._err(request.id, "SERVER_BUSY"),)
        if request.method == _HOLD:
            self.inflight.add(request.id)
            return ()
        served = TAB_METHODS | SETTINGS_METHODS
        if request.method == _SNAPSHOT or (self.tabs is not None and request.method in served):
            return self._tab(request)
        return (self._err(request.id, "METHOD_NOT_FOUND"),)

    def _tab(self, request: Request) -> tuple[bytes, ...]:
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
        return (self._ok(request.id, result),)

    def _snapshot(self, request: Request, tabs: TabService) -> tuple[bytes, ...]:
        try:
            _ = msgspec.json.decode(request.params, type=EmptyParams)
        except msgspec.DecodeError:
            return (self._err(request.id, "INVALID_PARAMS"),)
        result = SnapshotResult(snapshot=tabs.snapshot(), seq=self.ring.current_seq)
        return (self._ok(request.id, result),)

    def _notification(self, note: Notification) -> tuple[bytes, ...]:
        if note.method.startswith(_SIDECAR_PREFIX):
            # Terminal telemetry. Only an authenticated sidecar is heard; the rest is dropped.
            return ()
        if note.method != "$/cancelRequest":
            return ()
        params = msgspec.json.decode(note.params, type=CancelParams)
        if params.id not in self.inflight:
            return ()
        self.inflight.discard(params.id)
        return (self._err(params.id, "REQUEST_CANCELLED"),)

    def _initialize(self, request: Request) -> tuple[bytes, ...]:
        if self.ready:
            return (self._err(request.id, "ALREADY_INITIALIZED"),)
        params = msgspec.json.decode(request.params, type=InitializeParams)
        chosen = _negotiate(params.protocol[0], params.protocol[1])
        if chosen is None:
            return (self._err(request.id, "PROTOCOL_VERSION_UNSUPPORTED"),)
        if params.role is ClientRole.SIDECAR and not self._sidecar_ok(params):
            return (self._err(request.id, "SIDECAR_TOKEN_INVALID"),)
        self.ready = True
        self.role = params.role
        result = InitializeResult(
            protocol=chosen,
            server_version="0.0.0",
            server_pid=0,
            session_id=self.session_id,
            capabilities=_SERVER_CAPS & params.capabilities,
            limits=self._limits,
        )
        return (self._ok(request.id, result),)

    def _sidecar_ok(self, params: InitializeParams) -> bool:
        """The one-shot token from `tab/launchSpec`, for that tab, used once."""
        tabs = self.tabs
        if tabs is None or params.tab_id is None or params.sidecar_token is None:
            return False
        return tabs.claim_sidecar(str(params.tab_id), params.sidecar_token)

    def _subscribe(self, request: Request) -> tuple[bytes, ...]:
        params = msgspec.json.decode(request.params, type=SubscribeParams)
        replay = self.ring.since(params.after_seq)
        if replay is None:
            return (self._err(request.id, "RESYNC_REQUIRED"),)
        self.subscribed = True
        frames = [self._ok(request.id, SubscribeResult(current_seq=self.ring.current_seq))]
        frames.extend(self._event(item) for item in replay)
        self._queued += len(replay)
        return tuple(frames)

    def _ok(self, request_id: int, result: msgspec.Struct) -> bytes:
        body = ResponseOk(id=request_id, result=msgspec.Raw(self._encoder.encode(result)))
        return encode_line(self._encoder, body)

    def _err(self, request_id: int | None, name: str) -> bytes:
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
