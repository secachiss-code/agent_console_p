"""A0.3 spike: free-threaded core loop, WAL writer, four readers."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import queue
import socket
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import msgspec
import uvicorn
from sqlalchemy import Integer, String, create_engine, event, func, insert, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

CORE_MODULES = (
    "alembic",
    "mcp",
    "mcp.server.mcpserver",
    "msgspec",
    "ruamel.yaml",
    "sqlalchemy",
    "uvicorn",
    "watchdog",
)


class Ingest(msgspec.Struct, frozen=True, kw_only=True):
    seq: int
    payload: str


class Ack(msgspec.Struct, frozen=True, kw_only=True):
    ok: bool
    seq: int


class Base(DeclarativeBase):
    pass


class EventRow(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer)
    payload: Mapped[str] = mapped_column(String)


@dataclass(frozen=True, kw_only=True)
class Bench:
    events: int
    http: int
    stored: int
    event_p50_ms: float
    event_p99_ms: float
    http_p50_ms: float
    http_p99_ms: float
    event_per_s: float
    http_per_s: float
    elapsed_s: float


def import_core_dependencies() -> bool:
    """Import every core dependency. Return whether the GIL is enabled afterwards."""
    for name in CORE_MODULES:
        importlib.import_module(name)
    return bool(sys._is_gil_enabled())


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((pct / 100) * (len(ordered) - 1))
    return ordered[min(index, len(ordered) - 1)] * 1000


def _wal(dbapi_connection: object, _record: object) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=2000")
    cursor.close()


class _QuietServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> contextlib.AbstractContextManager[None]:
        yield None


class Spike:
    """Unix NDJSON ingest plus uvicorn, one writer thread, four readers."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.db_path = directory / "spike.sqlite"
        self.ndjson_path = directory / "ndjson.sock"
        self.http_path = directory / "http.sock"
        self._queue: queue.Queue[Ingest | None] = queue.Queue()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._threads: list[threading.Thread] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._async_stop: asyncio.Event | None = None
        self._writer = create_engine(f"sqlite:///{self.db_path}", pool_size=1, max_overflow=0)
        self._reader = create_engine(f"sqlite:///{self.db_path}", pool_size=4, max_overflow=0)
        event.listen(self._writer, "connect", _wal)
        event.listen(self._reader, "connect", _wal)
        Base.metadata.create_all(self._writer)

    def start(self) -> None:
        self._threads = [
            threading.Thread(target=self._write_loop, name="spike-writer", daemon=True),
            *(
                threading.Thread(target=self._read_loop, name=f"spike-reader-{index}", daemon=True)
                for index in range(4)
            ),
            threading.Thread(target=self._run_loop, name="spike-loop", daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        if not self._ready.wait(timeout=5):
            self.stop()
            raise RuntimeError("spike did not start")

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(None)
        loop = self._loop
        async_stop = self._async_stop
        if loop is not None and async_stop is not None:
            loop.call_soon_threadsafe(async_stop.set)
        for thread in self._threads:
            thread.join(timeout=5)
        self._writer.dispose()
        self._reader.dispose()

    def stored(self) -> int:
        with Session(self._reader) as session:
            count = session.scalar(select(func.count()).select_from(EventRow))
        return int(count or 0)

    def send_event(self, seq: int) -> float:
        started = time.perf_counter()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(str(self.ndjson_path))
            sock.sendall(msgspec.json.encode(Ingest(seq=seq, payload="x")) + b"\n")
            ack = _readline(sock)
        decoded = msgspec.json.decode(ack, type=Ack)
        if not decoded.ok or decoded.seq != seq:
            raise RuntimeError("bad ack")
        return time.perf_counter() - started

    def send_http(self) -> float:
        started = time.perf_counter()
        request = b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(str(self.http_path))
            sock.sendall(request)
            payload = bytearray()
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                payload.extend(chunk)
        if b" 200 " not in bytes(payload):
            raise RuntimeError("http status")
        return time.perf_counter() - started

    def benchmark(self, duration_s: float) -> Bench:
        event_latencies: list[float] = []
        http_latencies: list[float] = []
        errors: list[BaseException] = []

        def paced(rate: float, action: Callable[[int], float], sink: list[float]) -> None:
            interval = 1 / rate
            nxt = time.perf_counter()
            deadline = nxt + duration_s
            seq = 0
            while time.perf_counter() < deadline:
                seq += 1
                try:
                    sink.append(action(seq))
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)
                    return
                nxt += interval
                delay = nxt - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)

        started = time.perf_counter()
        workers = (
            threading.Thread(target=paced, args=(1000, self.send_event, event_latencies)),
            threading.Thread(target=paced, args=(200, lambda _seq: self.send_http(), http_latencies)),
        )
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        elapsed = time.perf_counter() - started
        if errors:
            raise errors[0]
        self._wait_stored(len(event_latencies))
        return Bench(
            events=len(event_latencies),
            http=len(http_latencies),
            stored=self.stored(),
            event_p50_ms=_percentile(event_latencies, 50),
            event_p99_ms=_percentile(event_latencies, 99),
            http_p50_ms=_percentile(http_latencies, 50),
            http_p99_ms=_percentile(http_latencies, 99),
            event_per_s=len(event_latencies) / elapsed,
            http_per_s=len(http_latencies) / elapsed,
            elapsed_s=elapsed,
        )

    def _wait_stored(self, expected: int) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.stored() >= expected:
                return
            time.sleep(0.02)
        raise RuntimeError(f"stored {self.stored()} of {expected}")

    def _write_loop(self) -> None:
        batch: list[dict[str, object]] = []
        deadline = time.monotonic() + 0.05
        with Session(self._writer) as session:
            while True:
                timeout = max(0.0, deadline - time.monotonic())
                try:
                    item = self._queue.get(timeout=timeout or 0.05)
                except queue.Empty:
                    item = None
                    timed_out = True
                else:
                    timed_out = False
                if item is None and not timed_out:
                    self._flush(session, batch)
                    return
                if item is not None:
                    batch.append({"seq": item.seq, "payload": item.payload})
                if batch and (len(batch) >= 500 or time.monotonic() >= deadline):
                    self._flush(session, batch)
                    batch = []
                    deadline = time.monotonic() + 0.05
                elif not batch:
                    deadline = time.monotonic() + 0.05

    def _flush(self, session: Session, batch: list[dict[str, object]]) -> None:
        if not batch:
            return
        session.execute(insert(EventRow), batch)
        session.commit()

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            with Session(self._reader) as session:
                session.scalar(select(func.count()).select_from(EventRow))
            time.sleep(0.01)

    def _run_loop(self) -> None:
        asyncio.run(self._async_main())

    async def _async_main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._async_stop = asyncio.Event()
        server = await asyncio.start_unix_server(self._ndjson, path=str(self.ndjson_path))
        config = uvicorn.Config(
            _HttpApp(),
            uds=str(self.http_path),
            loop="none",
            lifespan="on",
            log_config=None,
            access_log=False,
            interface="asgi3",
        )
        http = _QuietServer(config)
        http_task = asyncio.create_task(http.serve())
        while not http.started:
            await asyncio.sleep(0.01)
        self._ready.set()
        await self._async_stop.wait()
        server.close()
        await server.wait_closed()
        http.should_exit = True
        await http_task

    async def _ndjson(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            line = await reader.readline()
            if not line:
                break
            event = msgspec.json.decode(line, type=Ingest)
            self._queue.put(event)
            writer.write(msgspec.json.encode(Ack(ok=True, seq=event.seq)) + b"\n")
            await writer.drain()
        writer.close()

class _HttpApp:
    async def __call__(
        self,
        scope: dict[str, object],
        receive: Callable[..., Awaitable[object]],
        send: Callable[..., Awaitable[object]],
    ) -> None:
        if scope.get("type") == "lifespan":
            await _lifespan(receive, send)
            return
        await send(
            {"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]}
        )
        await send({"type": "http.response.body", "body": b"ok"})


async def _lifespan(receive: Callable[..., object], send: Callable[..., object]) -> None:
    while True:
        message = await receive()
        if not isinstance(message, dict):
            return
        kind = message.get("type")
        if kind == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif kind == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


def _readline(sock: socket.socket) -> bytes:
    data = bytearray()
    while not data.endswith(b"\n"):
        chunk = sock.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)
