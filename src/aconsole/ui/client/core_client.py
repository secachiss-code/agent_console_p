"""Async NDJSON client. Calls stay on the GTK loop; reconnects after a drop."""

import asyncio
import contextlib
import fcntl
import os
from pathlib import Path

import msgspec

from aconsole.contracts import API_VERSION
from aconsole.contracts.app import EmptyParams, SnapshotResult, SubscribeParams, SubscribeResult
from aconsole.contracts.common import ClientRole
from aconsole.contracts.errors import ErrorObject
from aconsole.contracts.ipc import (
    CancelParams,
    Envelope,
    EventEnvelope,
    InitializeParams,
    InitializeResult,
    Notification,
    ProtocolVersion,
    Request,
    ResponseErr,
    ResponseOk,
)
from aconsole.contracts.methods import method_by_name
from aconsole.ui.client.store import CONNECTING_BANNER, CoreStore

_DEFAULT_TIMEOUT_S = 10.0
_LONG_TIMEOUT_S = 60.0
_CANCEL_GRACE_S = 1.0
_MS_PER_S = 1000
_READY_DEADLINE_S = 5.0
_CATCH_UP_S = 0.01
_CATCH_UP_DEADLINE_S = 1.0
_BACKOFF_S = (0.05, 0.1, 0.2, 0.4, 0.8)
_UI_CAPABILITIES = frozenset({"events.v1", "terminal.inject", "approvals.inline", "notifications.desktop"})


class CoreCallError(Exception):
    """The core answered with an error object."""

    def __init__(self, error: ErrorObject) -> None:
        self.error: ErrorObject = error
        super().__init__(f"{error.message} ({error.code})")


class CoreTimeoutError(Exception):
    """The request exceeded its timeout and was cancelled."""

    def __init__(self, method: str) -> None:
        self.method: str = method
        super().__init__(method)


class _KindOnly(msgspec.Struct, frozen=True, kw_only=True):
    kind: str


def core_paths(runtime_dir: Path) -> tuple[Path, Path]:
    """Socket and lock paths under an XDG runtime directory."""
    root = runtime_dir / "aconsole"
    return root / "core.sock", root / "core.lock"


def lock_held(path: Path) -> bool:
    """True when another process holds an exclusive flock on ``path``."""
    if not path.exists():
        return False
    descriptor = os.open(path, os.O_RDWR)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)


def _decode_line(line: bytes) -> Envelope:
    kind = msgspec.json.decode(line, type=_KindOnly).kind
    if kind == "request":
        return msgspec.json.decode(line, type=Request)
    if kind == "notification":
        return msgspec.json.decode(line, type=Notification)
    if kind == "result":
        return msgspec.json.decode(line, type=ResponseOk)
    if kind == "error":
        return msgspec.json.decode(line, type=ResponseErr)
    message = "core sent an unknown envelope"
    raise ConnectionError(message)


class CoreClient:
    """One UI connection. ``start_daemon`` launches ``aconsoled`` when the socket is missing."""

    path: str
    store: CoreStore
    timeout_s: float
    connects: int
    resyncs: int
    resync_count: int
    _reader: asyncio.StreamReader | None
    _writer: asyncio.StreamWriter | None
    _pending: dict[int, asyncio.Future[Envelope]]
    _next_id: int
    _closing: bool
    _write_lock: asyncio.Lock
    _read_task: asyncio.Task[None] | None
    _reconnect_task: asyncio.Task[None] | None
    _resync_task: asyncio.Task[None] | None
    _process: asyncio.subprocess.Process | None

    def __init__(self, path: str, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self.path = path
        self.store = CoreStore()
        self.timeout_s = timeout_s
        self.connects = 0
        self.resyncs = 0
        self.resync_count = 0
        self._reader = None
        self._writer = None
        self._pending = {}
        self._next_id = 1
        self._closing = False
        self._write_lock = asyncio.Lock()
        self._read_task = None
        self._reconnect_task = None
        self._resync_task = None
        self._process = None

    @property
    def daemon_pid(self) -> int | None:
        if self._process is None:
            return None
        return self._process.pid

    def timeout_for(self, method: str) -> float:
        if method_by_name(method).long:
            return _LONG_TIMEOUT_S
        return self.timeout_s

    async def connect(self) -> InitializeResult:
        await self._open_socket()
        return await self._handshake()

    async def start_daemon(self, argv: list[str], *, runtime: Path | None = None) -> InitializeResult:
        """Connect, or spawn ``argv`` when the core lock is free."""
        if runtime is not None:
            _socket, lock = core_paths(runtime)
            if lock_held(lock):
                return await self.connect()
        try:
            return await self.connect()
        except OSError:
            self._process = await asyncio.create_subprocess_exec(
                *argv,
                start_new_session=True,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        deadline = asyncio.get_running_loop().time() + _READY_DEADLINE_S
        delay = _BACKOFF_S[0]
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(delay)
            try:
                return await self.connect()
            except OSError:
                delay = min(delay * 2, _BACKOFF_S[-1])
        message = "core socket did not appear"
        raise ConnectionError(message)

    async def close(self) -> None:
        self._closing = True
        tasks = tuple(task for task in (self._read_task, self._reconnect_task, self._resync_task) if task is not None)
        for task in tasks:
            _ = task.cancel()
        writer = self._writer
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, ConnectionError, CoreCallError, CoreTimeoutError, OSError):
                await task
        self._drain_pending()
        if self._process is not None and self._process.returncode is None:
            _ = self._process.terminate()
            _ = await self._process.wait()

    def _drain_pending(self) -> None:
        pending = tuple(self._pending.values())
        self._pending.clear()
        for future in pending:
            if future.done() and not future.cancelled():
                _ = future.exception()
            elif not future.done():
                _ = future.cancel()

    async def request[T: msgspec.Struct](
        self,
        method: str,
        params: msgspec.Struct,
        result_type: type[T],
        *,
        long: bool = False,
    ) -> T:
        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Envelope] = loop.create_future()
        self._pending[request_id] = future
        timeout = _LONG_TIMEOUT_S if long else self.timeout_s
        body = Request(
            id=request_id,
            method=method,
            params=msgspec.Raw(msgspec.json.encode(params)),
            timeout_ms=int(timeout * _MS_PER_S),
        )
        await self._send(msgspec.json.encode(body) + b"\n")
        envelope = await self._wait(future, request_id, method, timeout)
        if isinstance(envelope, ResponseErr):
            raise CoreCallError(envelope.error)
        if not isinstance(envelope, ResponseOk):
            message = "core sent a notification where a response was required"
            raise ConnectionError(message)
        return msgspec.json.decode(envelope.result, type=result_type)

    async def pull_snapshot(self) -> tuple[SnapshotResult, SubscribeResult]:
        snapshot = await self.request("app/snapshot", EmptyParams(), SnapshotResult)
        self.store.apply_snapshot(snapshot.snapshot, snapshot.seq)
        subscribed = await self.request(
            "events/subscribe",
            SubscribeParams(after_seq=snapshot.seq),
            SubscribeResult,
        )
        # The read loop applies the burst after this response. Wait for it, and
        # for the resync it requests, before the UI paints a half-applied snapshot.
        await self._catch_up(subscribed.current_seq)
        return snapshot, subscribed

    async def _catch_up(self, current_seq: int) -> None:
        deadline = asyncio.get_running_loop().time() + _CATCH_UP_DEADLINE_S
        current = asyncio.current_task()
        while asyncio.get_running_loop().time() < deadline:
            task = self._resync_task
            if task is not None and not task.done() and task is not current:
                await task
                return
            if self.store.seq >= current_seq and not self.store.reconnecting:
                return
            await asyncio.sleep(_CATCH_UP_S)

    async def _wait(
        self,
        future: asyncio.Future[Envelope],
        request_id: int,
        method: str,
        limit_s: float,
    ) -> Envelope:
        try:
            return await asyncio.wait_for(asyncio.shield(future), limit_s)
        except TimeoutError:
            await self._send(_cancel_frame(request_id))
            try:
                return await asyncio.wait_for(future, _CANCEL_GRACE_S)
            except TimeoutError:
                _ = self._pending.pop(request_id, None)
                raise CoreTimeoutError(method) from None

    async def _handshake(self) -> InitializeResult:
        params = InitializeParams(
            protocol=(ProtocolVersion(major=1, minor=0), ProtocolVersion(major=1, minor=0)),
            role=ClientRole.UI,
            client_name="aconsole-ui",
            client_version=API_VERSION,
            pid=os.getpid(),
            capabilities=_UI_CAPABILITIES,
        )
        result = await self.request("initialize", params, InitializeResult)
        note = Notification(method="initialized", params=msgspec.Raw(b"{}"))
        await self._send(msgspec.json.encode(note) + b"\n")
        return result

    async def _open_socket(self) -> None:
        self._reader, self._writer = await asyncio.open_unix_connection(self.path)
        self.connects += 1
        self._read_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        reader = self._reader
        if reader is None:
            return
        try:
            while not self._closing:
                line = await reader.readline()
                if line == b"":
                    break
                self._dispatch_line(line)
        finally:
            self._fail_pending()
            if not self._closing:
                self.store.banner = CONNECTING_BANNER
                self.store.reconnecting = True
                self._reconnect_task = asyncio.create_task(self._reconnect())

    def _dispatch_line(self, line: bytes) -> None:
        envelope = _decode_line(line)
        if isinstance(envelope, ResponseErr):
            if envelope.id is not None:
                self._finish(envelope.id, envelope)
            return
        if isinstance(envelope, ResponseOk):
            self._finish(envelope.id, envelope)
            return
        if isinstance(envelope, Notification) and envelope.method == "$/event":
            event = msgspec.json.decode(envelope.params, type=EventEnvelope)
            if not self.store.apply(event) and self.resyncs == 0:
                self.resyncs = 1
                self._resync_task = asyncio.create_task(self._resync())

    def _finish(self, request_id: int, envelope: Envelope) -> None:
        future = self._pending.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(envelope)

    def _fail_pending(self) -> None:
        error = ConnectionError("core closed the socket")
        pending = tuple(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(error)

    async def _resync(self) -> None:
        self.resync_count += 1
        try:
            _ = await self.pull_snapshot()
        except ConnectionError, CoreCallError, CoreTimeoutError, OSError:
            self.store.banner = CONNECTING_BANNER
            self.store.reconnecting = True
            return
        self.resyncs = 0

    async def _reconnect(self) -> None:
        delay = _BACKOFF_S[0]
        while not self._closing:
            await asyncio.sleep(delay)
            try:
                await self._open_socket()
                _ = await self._handshake()
                self.resyncs = 0
                await self._resync()
            except ConnectionError, CoreCallError, CoreTimeoutError, OSError:
                delay = min(delay * 2, _BACKOFF_S[-1])
                continue
            return

    async def _send(self, payload: bytes) -> None:
        writer = self._writer
        if writer is None:
            message = "core socket is not open"
            raise ConnectionError(message)
        async with self._write_lock:
            writer.write(payload)
            await writer.drain()


def _cancel_frame(request_id: int) -> bytes:
    params = CancelParams(id=request_id)
    note = Notification(method="$/cancelRequest", params=msgspec.Raw(msgspec.json.encode(params)))
    return msgspec.json.encode(note) + b"\n"
