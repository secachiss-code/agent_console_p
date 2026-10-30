"""Composition root for `aconsoled`: one process, then idle shutdown."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import os
import signal
import socket as sockets
import struct
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec
import uvicorn

from aconsole.contracts.common import ClientRole
from aconsole.contracts.errors import ErrorData, ErrorObject, MessageTooLargeDetails, error_by_name
from aconsole.contracts.events import TabAdded, TabRemoved, UiEvent
from aconsole.contracts.ipc import InitializeParams, Request, ResponseErr
from aconsole.core.application.services.tabs import TabService
from aconsole.core.domain.clock import Clock
from aconsole.core.infrastructure.http.app import IngestApp, serve_config
from aconsole.core.infrastructure.http.auth import TokenBook
from aconsole.core.infrastructure.http.consumer import consume
from aconsole.core.infrastructure.http.routes import IngestItem, IngestQueue
from aconsole.core.infrastructure.ipc.framing import (
    MAX_MESSAGE_BYTES,
    MessageTooLargeError,
    decode_envelope,
    encode_line,
    read_frame,
)
from aconsole.core.infrastructure.ipc.ring import EventRing
from aconsole.core.infrastructure.ipc.server import allow_peer, peer_uid
from aconsole.core.infrastructure.ipc.session import Connection
from aconsole.core.infrastructure.mcp.server import TabTokens, beside_ingest, build_server, http_app
from aconsole.core.infrastructure.mcp.tools import PlanBoard, TabContext
from aconsole.core.infrastructure.persistence.cleanup import sweep
from aconsole.core.infrastructure.persistence.engine import write_engine
from aconsole.core.infrastructure.persistence.repositories.tabs import TabDatabase
from aconsole.core.infrastructure.persistence.startup import prepare_database, sqlite_url
from aconsole.core.infrastructure.persistence.writer import Writer
from aconsole.core.infrastructure.tabs import HostTabs

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.server.mcpserver import MCPServer
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

    from aconsole.contracts.ipc import EventEnvelope

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


def release_lock(descriptor: int) -> None:
    """Unlock and close. The next process can take the lock."""
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def credentials_uid(sock: object) -> int:
    """Peer uid, or -1 when the socket has no credentials."""
    try:
        return peer_uid(sock)
    except AttributeError, OSError, TypeError, struct.error:
        return -1


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


def _too_large(encoder: msgspec.json.Encoder, limit: int) -> bytes:
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
    return encode_line(encoder, ResponseErr(id=None, error=error))


class Daemon:
    """The core process. A second instance refuses the lock. Idle clients shut it down."""

    def __init__(
        self,
        socket: Path,
        *,
        idle_s: float = IDLE_EXIT_S,
        live_tabs: int = 0,
        frame_limit: int = MAX_MESSAGE_BYTES,
        database: Path | None = None,
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
        self._holders: int = 0
        self._listening: bool = False
        self._closing: bool = False
        self._stop_requested: bool = False
        self._stopped: asyncio.Event | None = None
        self._server: asyncio.AbstractServer | None = None
        self._idle: asyncio.Task[None] | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._ring: EventRing = EventRing()
        self._host: HostTabs = HostTabs(socket_path=str(socket))
        self.tabs: TabService = TabService(Clock(), self._host, listener=self._from_worker)
        self._encoder: msgspec.json.Encoder = msgspec.json.Encoder()
        # Tab calls block (podman, git, the 3 s stop wait): one worker thread runs them in order, off the loop.
        self._worker: ThreadPoolExecutor | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connections: dict[Connection, asyncio.StreamWriter] = {}
        # Hooks reach the core over HTTP on 127.0.0.1 (INGEST_HTTP). Off by default so tests stay socket-only.
        self.serve_ingest: bool = False
        self.ingest_port: int | None = None
        self._tokens: TokenBook = TokenBook()
        self._plans: PlanBoard = PlanBoard()
        self._mcp_tokens: TabTokens = TabTokens()
        self._mcp: MCPServer = build_server(
            self._plans,
            TabContext(tab_id="local", project_id="local", worktree=None, agent="claude"),
        )
        self._ingest_queue: IngestQueue = IngestQueue()
        self._ingest_ready: asyncio.Event | None = None
        self._ingest_tasks: list[asyncio.Task[None]] = []
        self._uvicorn: uvicorn.Server | None = None
        self.database: Path | None = database
        self.sweep_every_s: float = SWEEP_EVERY_S
        self._engine: Engine | None = None
        self._writer: Writer | None = None
        self._sweeper: asyncio.Task[None] | None = None
        # Checkpoint refs whose rows the sweep removed. Deleting the git refs belongs to the checkpoint service (A4).
        self.dropped_refs: list[str] = []

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
        try:
            await self._open_database()
            await self._restore_tabs()
            await self._open_ingest()
            await self._listen()
            self._listening = True
            if self._quiet() and not self._stopped.is_set():
                self._arm_idle()
            _ = await self._stopped.wait()
        finally:
            self._closing = True
            self._listening = False
            self._cancel_idle()
            try:
                await self._close_server()
                await self._close_ingest()
                await self._close_database()
            finally:
                self._worker.shutdown(wait=False, cancel_futures=True)
                self._worker = None
                release_lock(descriptor)
        return 0

    def _from_worker(self, event: UiEvent) -> None:
        """Tab listener. Runs on the worker; the live count is read there, where the tab book is consistent."""
        live = self.tabs.running_count()
        token = self.tabs.ingest_token(str(event.tab.id)) if isinstance(event, TabAdded) else None
        loop = self._loop
        if loop is not None and not loop.is_closed():
            _ = loop.call_soon_threadsafe(self._publish, event, live, token)

    def _publish(self, event: UiEvent, live: int, token: str | None) -> None:
        """Number the event once, send it to every subscribed connection, and keep tokens and liveness current."""
        if isinstance(event, TabAdded) and token is not None:
            tab_id = str(event.tab.id)
            self._tokens.register(tab_id, token)
            worktree = event.tab.worktree_path
            self._mcp_tokens.register(
                tab_id,
                token,
                TabContext(
                    tab_id=tab_id,
                    project_id=str(event.tab.project_id),
                    worktree=Path(worktree) if worktree else None,
                    agent=str(event.tab.agent),
                ),
            )
        if isinstance(event, TabRemoved):
            self._tokens.drop(str(event.tab_id))
            self._mcp_tokens.drop(str(event.tab_id))
        item = self._ring.append(event, datetime.now(UTC))
        for connection, writer in tuple(self._connections.items()):
            self._deliver(connection, writer, item)
        if self._listening:
            self.set_live_tabs(live)

    @staticmethod
    def _deliver(connection: Connection, writer: asyncio.StreamWriter, item: EventEnvelope) -> None:
        if writer.is_closing():
            return
        for frame in connection.deliver(item):
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

        async def wake(item: IngestItem) -> None:
            del item
            ready.set()

        app = beside_ingest(
            IngestApp(self._tokens, self._ingest_queue, observer=wake),
            http_app(self._mcp),
            self._mcp_tokens,
        )
        listener = sockets.socket(sockets.AF_INET, sockets.SOCK_STREAM)
        listener.setsockopt(sockets.SOL_SOCKET, sockets.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])  # pyright: ignore[reportAny] -- AF_INET address is (host, port)
        server = uvicorn.Server(serve_config(app, port=port))
        self._uvicorn = server
        self.ingest_port = port
        self._host.set_ingest_port(port)
        self._ingest_tasks = [
            asyncio.create_task(server.serve(sockets=[listener])),
            asyncio.create_task(consume(self._ingest_queue, ready, self._apply_hook)),
        ]
        # uvicorn exposes only a `started` flag, no event to wait on.
        while not server.started and not self._ingest_tasks[0].done():  # noqa: ASYNC110
            await asyncio.sleep(0.01)

    async def _apply_hook(self, tab_id: str, event_name: str) -> None:
        _ = await self._in_worker(lambda: self.tabs.apply_hook(tab_id, event_name))

    async def _close_ingest(self) -> None:
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
        self.tabs.use_store(store)
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

    async def _sweep_loop(self, writer: Writer) -> None:
        while True:
            try:
                _ = await writer.submit(self._sweep_once)
            except Exception:  # noqa: BLE001 -- a failed sweep is retried next period; the core keeps serving
                _LOG.exception("retention sweep failed")
            await asyncio.sleep(self.sweep_every_s)

    def _sweep_once(self, session: Session) -> object:
        return sweep(session, now=datetime.now(UTC), drop_ref=self.dropped_refs.append)

    async def _close_database(self) -> None:
        sweeper = self._sweeper
        self._sweeper = None
        if sweeper is not None:
            _ = sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
        writer = self._writer
        self._writer = None
        if writer is not None:
            await asyncio.to_thread(writer.stop)
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
        try:
            payloads = await self._in_worker(lambda: connection.receive(frame))
        except msgspec.DecodeError:
            return False
        for payload in payloads:
            writer.write(payload)
            await writer.drain()
        connection.drained()
        return True

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        raw = writer.get_extra_info("socket")  # pyright: ignore[reportAny] -- asyncio types this as Any
        if not allow_peer(credentials_uid(raw)):  # pyright: ignore[reportAny] -- asyncio types this as Any
            writer.close()
            await writer.wait_closed()
            return
        connection = Connection(self._ring, tabs=self.tabs)
        counted = False
        self._writers.add(writer)
        self._connections[connection] = writer
        try:
            while True:
                try:
                    frame = await read_frame(reader, limit=self._frame_limit)
                except MessageTooLargeError:
                    writer.write(_too_large(self._encoder, self._frame_limit))
                    await writer.drain()
                    break
                if frame is None:
                    break
                was_ready = connection.ready
                role = initialize_role(frame)
                if not await self._answer(connection, writer, frame):
                    break
                if counted or was_ready or role not in _HOLDS or not connection.ready:
                    continue
                counted = True
                self._gain_holder()
        finally:
            self._writers.discard(writer)
            _ = self._connections.pop(connection, None)
            if counted:
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
