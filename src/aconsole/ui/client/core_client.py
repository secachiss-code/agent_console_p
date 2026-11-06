"""Async NDJSON client. Calls stay on the GTK loop; reconnects after a drop."""

import asyncio
import contextlib
import fcntl
import logging
import os
import signal
from pathlib import Path

import msgspec

from aconsole.contracts import API_VERSION
from aconsole.contracts.app import EmptyParams, SnapshotResult, SubscribeParams, SubscribeResult
from aconsole.contracts.common import ClientRole
from aconsole.contracts.errors import ErrorData, ErrorObject, ProtocolVersionUnsupportedDetails, error_by_name
from aconsole.contracts.ipc import (
    CancelParams,
    Envelope,
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
from aconsole.contracts.methods import method_by_name
from aconsole.ui.client.error_text import core_log_path, error_text
from aconsole.ui.client.store import CoreStore

_LOG = logging.getLogger(__name__)
_READ_LIMIT = ProtocolLimits().max_message_bytes + 1
_DEFAULT_TIMEOUT_S = 10.0
_LONG_TIMEOUT_S = 60.0
_CANCEL_GRACE_S = 1.0
_MS_PER_S = 1000
# The first start upgrades the database before the socket opens.
_READY_DEADLINE_S = 20.0
_CATCH_UP_S = 0.01
_CATCH_UP_DEADLINE_S = 1.0
_BACKOFF_S = (0.05, 0.1, 0.2, 0.4, 0.8)
_UI_CAPABILITIES = frozenset(
    {
        "events.v1",
        "approvals.inline",
        "terminal.inject",
        "notifications.desktop",
        "diff.v1",
        "checkpoints.v1",
        "work_sessions.v1",
    }
)


def _core_log() -> int:
    """Where the core's stderr goes. Without a place to write, the core must still start."""
    path = core_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # An empty log is left in place: a start that came to nothing must not push the last real log out.
        if path.exists() and path.stat().st_size > 0:
            _ = path.replace(path.with_suffix(".log.1"))
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC, 0o600)
    except OSError:
        _LOG.warning("cannot open the core log %s", path, exc_info=True)
        return asyncio.subprocess.DEVNULL


class CoreCallError(Exception):
    """The core answered with an error object."""

    def __init__(self, error: ErrorObject) -> None:
        self.error: ErrorObject = error
        super().__init__(error_text(error))


class CoreTimeoutError(Exception):
    """The request exceeded its timeout and was cancelled."""

    def __init__(self, method: str) -> None:
        self.method: str = method
        super().__init__(method)


# The four envelopes are tagged by `kind`, so one pass picks the type and decodes it.
_ENVELOPE: msgspec.json.Decoder[Envelope] = msgspec.json.Decoder(Envelope)
_EVENT = msgspec.json.Decoder(EventEnvelope)


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
    return _ENVELOPE.decode(line)


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
    keep_daemon: bool

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
        self._daemon_argv: tuple[str, ...] | None = None
        self._daemon_runtime: Path | None = None
        # The fake dies with its UI. The real core stays until its idle timer, so a new window finds it.
        self.keep_daemon = False

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
        self._closing = False
        await self._open_socket()
        return await self._handshake()

    async def start_daemon(self, argv: list[str], *, runtime: Path | None = None) -> InitializeResult:
        """Connect, or spawn ``argv`` when the core lock is free.

        A held lock means a core is already starting. This waits for its socket and does not spawn another.
        """
        self._daemon_argv = tuple(argv)
        self._daemon_runtime = runtime
        if runtime is not None:
            _socket, lock = core_paths(runtime)
            if lock_held(lock):
                return await self._wait_for_socket()
        try:
            return await self.connect()
        except OSError:
            log = _core_log()
            try:
                self._process = await asyncio.create_subprocess_exec(
                    *argv,
                    start_new_session=True,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=log,
                )
            finally:
                if log >= 0:
                    os.close(log)
        return await self._wait_for_socket()

    async def _wait_for_socket(self) -> InitializeResult:
        """Retry until the socket accepts a handshake, or the ready deadline passes."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _READY_DEADLINE_S
        delay = _BACKOFF_S[0]
        while True:
            try:
                return await self.connect()
            except OSError:
                if loop.time() >= deadline:
                    message = "core socket did not appear"
                    raise ConnectionError(message) from None
            remaining = deadline - loop.time()
            await asyncio.sleep(min(delay, remaining))
            delay = min(delay * 2, _BACKOFF_S[-1])

    async def replace_daemon(self, pid: int, argv: list[str], *, runtime: Path) -> InitializeResult:
        """Stop the core on ``pid`` and start ``argv``. Agents keep running; the new core restores the tabs.

        A core that does not let go of its lock in time stays, and this connects to it again.
        """
        await self.close()
        self._closing = False
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
        _socket, lock = core_paths(runtime)
        deadline = asyncio.get_running_loop().time() + _READY_DEADLINE_S
        while lock_held(lock) and asyncio.get_running_loop().time() < deadline:  # noqa: ASYNC110 -- the lock is held by another process, nothing here can signal an Event
            await asyncio.sleep(_BACKOFF_S[0])
        return await self.start_daemon(argv, runtime=runtime)

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
        if self._process is not None and self._process.returncode is None and not self.keep_daemon:
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
    ) -> T:
        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Envelope] = loop.create_future()
        self._pending[request_id] = future
        timeout = self.timeout_for(method)
        body = Request(
            id=request_id,
            method=method,
            params=msgspec.Raw(msgspec.json.encode(params)),
            timeout_ms=int(timeout * _MS_PER_S),
        )
        try:
            await self._send(msgspec.json.encode(body) + b"\n")
            envelope = await self._wait(future, request_id, method, timeout)
        finally:
            _ = self._pending.pop(request_id, None)
            if future.done() and not future.cancelled():
                _ = future.exception()
            elif not future.done():
                _ = future.cancel()
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
        if "work_sessions.v1" not in result.capabilities:
            error = error_by_name("PROTOCOL_VERSION_UNSUPPORTED")
            raise CoreCallError(ErrorObject(
                code=error.code, message=error.name,
                data=ErrorData(
                    reason=error.name, domain="aconsole.ipc", retryable=False,
                    user_message_key="error.protocol_version_unsupported", user_message_args={},
                    hint="Restart the core with the same AConsole version as the UI",
                    details=ProtocolVersionUnsupportedDetails(supported=("work_sessions.v1",)),
                ),
            ))
        note = Notification(method="initialized", params=msgspec.Raw(b"{}"))
        await self._send(msgspec.json.encode(note) + b"\n")
        return result

    async def _open_socket(self) -> None:
        self._reader, self._writer = await asyncio.open_unix_connection(self.path, limit=_READ_LIMIT)
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
        except ValueError:
            # A frame over the protocol limit: the stream cannot be resynchronised, only reopened.
            _LOG.exception("core sent a frame over %d bytes; reconnecting", _READ_LIMIT - 1)
        except msgspec.DecodeError:
            _LOG.exception("core sent a frame that does not decode; reconnecting")
        except (ConnectionError, OSError) as exc:
            _LOG.warning("core connection failed: %s", exc)
        finally:
            if self._reader is reader:
                self._fail_pending()
                if not self._closing:
                    self.store.set_connecting()
                    if self._reconnect_task is None or self._reconnect_task.done():
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
            event = _EVENT.decode(envelope.params)
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
        """Pull a snapshot until one arrives, or until the connection it was asked on is gone.

        A lost connection ends the attempt because the read loop then starts ``_reconnect``, which resyncs itself.
        """
        self.resyncs = 1
        self.resync_count += 1
        _LOG.info("pulling a new snapshot (resync %d)", self.resync_count)
        reader = self._reader
        delay = _BACKOFF_S[0]
        while not self._closing and self._reader is reader:
            try:
                _ = await self.pull_snapshot()
            except ConnectionError, OSError:
                self._show_connecting()
                return
            except CoreCallError, CoreTimeoutError, msgspec.DecodeError:
                _LOG.warning("snapshot failed; retrying in %.2f s", delay, exc_info=True)
                self._show_connecting()
                await asyncio.sleep(delay)
                delay = min(delay * 2, _BACKOFF_S[-1])
                continue
            if not self.store.reconnecting:
                self.resyncs = 0
                return
            # The events after that snapshot left a gap again.
            await asyncio.sleep(delay)

    def _show_connecting(self) -> None:
        self.store.set_connecting()

    async def _reconnect(self) -> None:
        delay = _BACKOFF_S[0]
        while not self._closing:
            await asyncio.sleep(delay)
            try:
                writer = self._writer
                self._reader = None
                if writer is not None:
                    writer.close()
                    with contextlib.suppress(OSError):
                        await writer.wait_closed()
                if self._daemon_argv is None:
                    _ = await self.connect()
                else:
                    _ = await self.start_daemon(list(self._daemon_argv), runtime=self._daemon_runtime)
                self.resyncs = 0
                await self._resync()
                if self.store.reconnecting:
                    continue
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
