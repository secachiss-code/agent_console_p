"""Unix NDJSON server. `--fake` answers from a scenario instead of the daemon."""

import asyncio
import os
import threading
import time
from dataclasses import dataclass

import msgspec

from aconsole.contracts.app import (
    CheckpointListResult,
    ChecksResult,
    PaletteItemsResult,
    SnapshotResult,
    SubscribeResult,
)
from aconsole.contracts.errors import ApprovalExpiredDetails, ErrorObject, MethodNotFoundDetails, error_by_name
from aconsole.contracts.events import ResyncRequired, UiEvent
from aconsole.contracts.ipc import (
    CancelParams,
    EventEnvelope,
    InitializeResult,
    Notification,
    ProtocolLimits,
    ProtocolVersion,
    Request,
    ResponseErr,
    ResponseOk,
)
from aconsole.core.application.services.event_router import EventRouter
from aconsole.core.infrastructure.fake.results import result_for
from aconsole.core.infrastructure.fake.scenarios import (
    AT,
    demo_checkpoints,
    demo_diff,
    demo_events,
    demo_snapshot,
    empty_checks,
    empty_snapshot,
    error_object,
    launch_spec,
    named_error,
    palette_items,
    quota_critical_snapshot,
    stress_snapshot,
    waiting_snapshot,
)
from aconsole.core.infrastructure.ipc.framing import MessageTooLargeError, decode_envelope, encode_line, read_frame

_DROP_AFTER_S = 30.0
_POLL_S = 0.05
_GAP_SEQ = 5


@dataclass(frozen=True, kw_only=True)
class Script:
    name: str
    snapshot: SnapshotResult
    events: tuple[UiEvent, ...]
    palette_count: int
    errors: bool
    expire_approvals: bool = False


def load_script(name: str) -> Script:
    if name == "demo":
        snapshot = demo_snapshot()
        return Script(
            name=name,
            snapshot=SnapshotResult(snapshot=snapshot, seq=0),
            events=demo_events(snapshot),
            palette_count=40,
            errors=False,
        )
    if name == "approval-expired":
        return Script(
            name=name,
            snapshot=SnapshotResult(snapshot=demo_snapshot(), seq=0),
            events=(),
            palette_count=0,
            errors=False,
            expire_approvals=True,
        )
    builders = {
        "empty": empty_snapshot,
        "stress": stress_snapshot,
        "errors": empty_snapshot,
        "waiting": waiting_snapshot,
        "quota-critical": quota_critical_snapshot,
    }
    builder = builders.get(name)
    if builder is None:
        message = f"unknown fake scenario {name}"
        raise ValueError(message)
    return Script(
        name=name,
        snapshot=SnapshotResult(snapshot=builder(), seq=0),
        events=(),
        palette_count=0,
        errors=name == "errors",
    )


class FakeCore:
    """One scenario on one socket. Stop closes the listener."""

    path: str
    script: Script
    drop_after_s: float
    hold_s: float
    gap: bool
    _loop: asyncio.AbstractEventLoop
    _ready: threading.Event
    _stop: asyncio.Event
    _thread: threading.Thread
    _server: asyncio.Server | None

    def __init__(
        self,
        path: str,
        scenario: str,
        *,
        drop_after_s: float = _DROP_AFTER_S,
        hold_s: float = 0,
        gap: bool = False,
    ) -> None:
        self.path = path
        self.script = load_script(scenario)
        self.drop_after_s = drop_after_s
        self.hold_s = hold_s
        self.gap = gap
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._stop = asyncio.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._server = None

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(3):
            message = "fake core did not listen"
            raise RuntimeError(message)

    def stop(self) -> None:
        if self._server is None:
            return
        handle = self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout=3)
        if handle.cancelled():
            message = "fake core stop was cancelled"
            raise RuntimeError(message)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        server = await asyncio.start_unix_server(self._client, path=self.path)
        self._server = server
        self._ready.set()
        _ = await self._stop.wait()
        server.close()
        await server.wait_closed()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        session = _Session(self.script, time.monotonic(), self.drop_after_s, gap=self.gap)
        cancelled: set[int] = set()
        try:
            while not self._stop.is_set():
                line = await self._read_line(reader)
                if line is None:
                    break
                if line == b"":
                    if session.expired(time.monotonic()):
                        break
                    continue
                if await self._maybe_cancel(reader, writer, session, line, cancelled):
                    continue
                for payload in session.handle(line):
                    writer.write(payload)
                    await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def _read_line(self, reader: asyncio.StreamReader) -> bytes | None:
        try:
            line = await asyncio.wait_for(read_frame(reader), timeout=_POLL_S)
        except TimeoutError:
            return b""
        except MessageTooLargeError:
            return None
        if line is None or line == b"":
            return None
        return line

    async def _maybe_cancel(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        session: _Session,
        line: bytes,
        cancelled: set[int],
    ) -> bool:
        """Hold a request until ``$/cancelRequest`` or ``hold_s``. True means the line was consumed."""
        try:
            envelope = decode_envelope(line)
        except msgspec.DecodeError:
            return False
        if isinstance(envelope, Notification):
            self._note_cancel(envelope, cancelled)
            return True
        if not isinstance(envelope, Request) or self.hold_s <= 0 or envelope.method == "initialize":
            return False
        await self._pause_for_cancel(reader, cancelled, envelope.id)
        if envelope.id not in cancelled:
            return False
        writer.write(_error_line(session.encoder, envelope.id, _cancelled_error()))
        await writer.drain()
        return True

    async def _pause_for_cancel(self, reader: asyncio.StreamReader, cancelled: set[int], request_id: int) -> None:
        deadline = asyncio.get_running_loop().time() + self.hold_s
        while request_id not in cancelled:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            try:
                line = await asyncio.wait_for(read_frame(reader), timeout=remaining)
            except TimeoutError:
                return
            if not line:
                return
            try:
                envelope = decode_envelope(line)
            except msgspec.DecodeError:
                continue
            if isinstance(envelope, Notification):
                self._note_cancel(envelope, cancelled)

    def _note_cancel(self, envelope: Notification, cancelled: set[int]) -> None:
        if envelope.method != "$/cancelRequest":
            return
        params = msgspec.json.decode(envelope.params, type=CancelParams)
        cancelled.add(params.id)


class _AfterSeq(msgspec.Struct, frozen=True, kw_only=True):
    """Subscribe params. An empty object means ``after_seq`` 0."""

    after_seq: int = 0


def _after_seq(request: Request) -> int:
    try:
        return msgspec.json.decode(request.params, type=_AfterSeq).after_seq
    except msgspec.DecodeError:
        return 0


def _ring_end(ring: tuple[EventEnvelope, ...]) -> int:
    if not ring:
        return 0
    return max(item.seq for item in ring)


def _error_line(encoder: msgspec.json.Encoder, request_id: int | None, error: ErrorObject) -> bytes:
    return encode_line(encoder, ResponseErr(id=request_id, error=error))


def _cancelled_error() -> ErrorObject:
    spec = error_by_name("REQUEST_CANCELLED")
    return ErrorObject(code=spec.code, message=spec.name)


class _Session:
    script: Script
    started: float
    drop_after_s: float
    gap: bool
    handshake: bool
    error_index: int
    router: EventRouter
    encoder: msgspec.json.Encoder
    _ring: tuple[EventEnvelope, ...] | None

    def __init__(self, script: Script, started: float, drop_after_s: float, *, gap: bool) -> None:
        self.script = script
        self.started = started
        self.drop_after_s = drop_after_s
        self.gap = gap
        self.handshake = False
        self.error_index = 0
        self.router = EventRouter()
        self.encoder = msgspec.json.Encoder()
        self._ring = None

    def expired(self, now: float) -> bool:
        return now - self.started >= self.drop_after_s

    def handle(self, line: bytes) -> tuple[bytes, ...]:
        try:
            envelope = decode_envelope(line)
        except msgspec.DecodeError:
            return (self._error(None, error_object(0)),)
        if not isinstance(envelope, Request):
            return (self._error(None, error_object(0)),)
        if envelope.method != "initialize" and not self.handshake:
            return (self._error(envelope.id, error_object(0)),)
        if self.script.errors and not self._open_despite_errors(envelope):
            return (self._error(envelope.id, self._next_error()),)
        if self.script.expire_approvals and envelope.method == "approval/decide":
            expired = named_error("APPROVAL_EXPIRED", ApprovalExpiredDetails(request_id="approval"))
            return (self._error(envelope.id, expired),)
        return self._dispatch(envelope)

    def _open_despite_errors(self, request: Request) -> bool:
        if request.method == "initialize" and not self.handshake:
            return True
        return self.gap and request.method in {"app/snapshot", "events/subscribe"}

    def _dispatch(self, request: Request) -> tuple[bytes, ...]:
        if request.method == "events/subscribe":
            return self._subscribe(request)
        built = self._struct(request)
        if built is None:
            missing = named_error("METHOD_NOT_FOUND", MethodNotFoundDetails(method=request.method))
            return (self._error(request.id, missing),)
        return (self._ok(request.id, built),)

    def _struct(self, request: Request) -> msgspec.Struct | None:
        method = request.method
        built: msgspec.Struct | None
        if method == "initialize":
            built = self._initialize()
        elif method == "app/snapshot":
            built = self._snapshot_result()
        elif method == "palette/items":
            built = PaletteItemsResult(items=palette_items(self.script.palette_count))
        elif method == "tab/launchSpec":
            built = launch_spec()
        elif method == "setup/checks":
            built = self._checks()
        elif method == "diff/summary":
            built = demo_diff()
        elif method == "checkpoint/list":
            built = self._checkpoints()
        else:
            built = result_for(method)
        return built

    def _checks(self) -> ChecksResult:
        checks = empty_checks() if self.script.name == "empty" else ()
        return ChecksResult(checks=checks)

    def _initialize(self) -> InitializeResult:
        self.handshake = True
        return InitializeResult(
            protocol=ProtocolVersion(major=1, minor=0),
            server_version="0.0.0",
            server_pid=os.getpid(),
            session_id="01JB2Z00000000000000000099",
            capabilities=frozenset({"events.v1"}),
            limits=ProtocolLimits(),
        )

    def _snapshot_result(self) -> SnapshotResult:
        # The first snapshot stays at seq 0 so the burst is delivered. Once the
        # ring exists, its seq is the ring's end: a resync subscribes there and
        # must not apply the same scripted events again.
        base = self.script.snapshot
        ring = self._ring
        if ring is None:
            return base
        return msgspec.structs.replace(base, seq=_ring_end(ring))

    def _events(self) -> tuple[EventEnvelope, ...]:
        ring = self._ring
        if ring is not None:
            return ring
        published = [self.router.publish(event, AT) for event in self.script.events]
        flushed = self.router.flush(AT)
        built = tuple(item for batch in published for item in batch) + flushed
        if self.gap:
            skewed = EventEnvelope(seq=_GAP_SEQ, at=AT, event=ResyncRequired(reason="restart"))
            built = (*built, skewed)
        self._ring = built
        return built

    def _subscribe(self, request: Request) -> tuple[bytes, ...]:
        after_seq = _after_seq(request)
        ring = self._events()
        current = _ring_end(ring)
        pending = tuple(item for item in ring if item.seq > after_seq)
        frames = [self._ok(request.id, SubscribeResult(current_seq=current))]
        frames.extend(self._event(item) for item in pending)
        return tuple(frames)

    def _checkpoints(self) -> CheckpointListResult:
        tabs = self.script.snapshot.snapshot.tabs
        points = demo_checkpoints(tabs[0].id) if tabs else ()
        return CheckpointListResult(checkpoints=points)

    def _next_error(self) -> ErrorObject:
        found = error_object(self.error_index)
        self.error_index += 1
        return found

    def _ok(self, request_id: int, result: msgspec.Struct) -> bytes:
        response = ResponseOk(id=request_id, result=msgspec.Raw(self.encoder.encode(result)))
        return encode_line(self.encoder, response)

    def _error(self, request_id: int | None, error: ErrorObject) -> bytes:
        return _error_line(self.encoder, request_id, error)

    def _event(self, envelope: EventEnvelope) -> bytes:
        note = Notification(method="$/event", params=msgspec.Raw(self.encoder.encode(envelope)))
        return encode_line(self.encoder, note)
