"""Round 6 K2.2: writer batch window, IPC drain, startup database, and migration parity."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec
import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Column, Integer, MetaData, Table, event, inspect, select
from sqlalchemy.orm import Session

from aconsole.contracts.app import SubscribeParams
from aconsole.contracts.common import ClientRole
from aconsole.contracts.events import Notify
from aconsole.contracts.ipc import (
    InitializeParams,
    Notification,
    ProtocolVersion,
    Request,
    ResponseOk,
)
from aconsole.core.bootstrap import Daemon
from aconsole.core.domain.agent import AgentKind
from aconsole.core.domain.tab_status import TabStatus
from aconsole.core.infrastructure.ipc.framing import decode_envelope, encode_line
from aconsole.core.infrastructure.ipc.ring import EventRing
from aconsole.core.infrastructure.ipc.server import IpcServer
from aconsole.core.infrastructure.ipc.session import Connection
from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from aconsole.core.infrastructure.persistence.models import Base, Project, QuotaHistory, Setting, Tab
from aconsole.core.infrastructure.persistence.startup import (
    backup,
    default_database,
    pending_upgrade,
    prepare_database,
    sqlite_url,
)
from aconsole.core.infrastructure.persistence.writer import Writer

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.engine import Engine

_WHEN = datetime(2026, 10, 26, tzinfo=UTC)
_NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
_TABLES = frozenset(Base.metadata.tables)
_PROJECT = "01JB2Z00000000000000000001"
_ENCODER = msgspec.json.Encoder()

# I1: a steady stream of jobs must not postpone the first commit.
_STREAM = 200
_GAP_S = 0.02
_FIRST_LIMIT_S = 0.150
# I1: a burst is committed in batches of at most 500 jobs.
_BURST = 5000
_BATCH = 500
# Alembic annotates compare_metadata as returning Any; this alias types the result as object.
_COMPARE: Callable[[MigrationContext, MetaData], object] = compare_metadata


def _tables(path: Path) -> set[str]:
    engine = write_engine(sqlite_url(path))
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _backups(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"{path.name}.*.bak"))


def _is_sqlite_file(path: Path) -> bool:
    """An SQLite database starts with its 16-byte header; an empty file is a valid empty database."""
    data = path.read_bytes()
    return data == b"" or data.startswith(b"SQLite format 3\x00")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[tuple[Writer, Engine]]:
    url = f"sqlite:///{tmp_path / 'store.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    writer = Writer(engine)
    writer.start()
    try:
        yield writer, engine
    finally:
        writer.stop()
        engine.dispose()


def _store(index: int, log: list[str]) -> Callable[[Session], object]:
    def _work(session: Session) -> object:
        session.add(Setting(key=f"probe.{index}", value={"n": index}, rev=1))
        log.append("job")
        return index

    return _work


def _count_commits(engine: Engine, log: list[str]) -> None:
    def _on_commit(_connection: object) -> None:
        log.append("commit")

    event.listen(engine, "commit", _on_commit)


def _batch_sizes(log: list[str]) -> list[int]:
    sizes: list[int] = []
    run = 0
    for entry in log:
        if entry == "commit":
            sizes.append(run)
            run = 0
        else:
            run += 1
    return sizes


async def _first_result_latency(writer: Writer, log: list[str]) -> float:
    started = time.perf_counter()
    first = writer.submit(_store(0, log))
    feeder = asyncio.create_task(_feed(writer, log))
    _ = await first
    latency = time.perf_counter() - started
    _ = await feeder
    return latency


async def _feed(writer: Writer, log: list[str]) -> None:
    futures: list[asyncio.Future[object]] = []
    for index in range(1, _STREAM):
        await asyncio.sleep(_GAP_S)
        futures.append(writer.submit(_store(index, log)))
    _ = await asyncio.gather(*futures)


async def _burst(writer: Writer, log: list[str]) -> list[object]:
    futures = [writer.submit(_store(index, log)) for index in range(_BURST)]
    return list(await asyncio.gather(*futures))


def test_round6_k22_window_starts_at_first_job(store: tuple[Writer, Engine]) -> None:
    writer, _engine = store
    log: list[str] = []
    latency = asyncio.run(_first_result_latency(writer, log))
    assert latency <= _FIRST_LIMIT_S
    assert log.count("job") == _STREAM


def test_round6_k22_burst_commits_in_batches(store: tuple[Writer, Engine]) -> None:
    writer, engine = store
    log: list[str] = []
    _count_commits(engine, log)
    results = asyncio.run(_burst(writer, log))
    assert results == list(range(_BURST))
    sizes = _batch_sizes(log)
    assert sum(sizes) == _BURST
    assert max(sizes) <= _BATCH
    assert len(sizes) == log.count("commit")
    assert len(sizes) >= _BURST // _BATCH


def _ready_connection() -> Connection:
    connection = Connection(EventRing(), outbound_limit=2)
    _ = connection.receive(_init())
    return connection


def _init() -> bytes:
    params = InitializeParams(
        protocol=(ProtocolVersion(major=1, minor=0), ProtocolVersion(major=1, minor=0)),
        role=ClientRole.UI,
        client_name="test",
        client_version="0.0.0",
        pid=1,
        capabilities=frozenset({"events.v1"}),
    )
    return encode_line(_ENCODER, Request(id=1, method="initialize", params=msgspec.Raw(_ENCODER.encode(params))))


def _hold(request_id: int) -> bytes:
    return encode_line(_ENCODER, Request(id=request_id, method="core/hold", params=msgspec.Raw(b"{}")))


def _notice(name: str) -> Notify:
    return Notify(tab_id=None, title=name, body=name, priority="normal", episode_id=name)


def _backpressure(frame: bytes) -> bool:
    envelope = decode_envelope(frame)
    return isinstance(envelope, Notification) and b"backpressure" in bytes(envelope.params)


def _subscribe(connection: Connection) -> None:
    """IPC §6: events reach a connection only after `events/subscribe`."""
    params = SubscribeParams(after_seq=connection.ring.current_seq)
    request = Request(id=2, method="events/subscribe", params=msgspec.Raw(_ENCODER.encode(params)))
    frame = decode_envelope(connection.receive(encode_line(_ENCODER, request))[0])
    assert isinstance(frame, ResponseOk)


def test_round6_k22_drained_resets_backpressure() -> None:
    connection = _ready_connection()
    _subscribe(connection)
    assert len(connection.push_event(_notice("a"), _WHEN)) == 1
    assert len(connection.push_event(_notice("b"), _WHEN)) == 1
    connection.drained()
    for name in ("c", "d"):
        frames = connection.push_event(_notice(name), _WHEN)
        assert len(frames) == 1
        assert not _backpressure(frames[0])
    assert not connection.paused


def test_round6_k22_without_drained_backpressure_fires() -> None:
    connection = _ready_connection()
    _subscribe(connection)
    _ = connection.push_event(_notice("a"), _WHEN)
    _ = connection.push_event(_notice("b"), _WHEN)
    stalled = connection.push_event(_notice("c"), _WHEN)
    assert len(stalled) == 1
    assert _backpressure(stalled[0])
    assert connection.paused


async def _drain_calls(path: Path, calls: list[int]) -> int:
    server = IpcServer(path / "aconsole" / "core.sock")
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.path)
        writer.write(_init())
        await writer.drain()
        _ = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=2)
        writer.write(_hold(2))
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        for _ in range(200):
            if len(calls) >= 2:
                break
            await asyncio.sleep(0.01)
    finally:
        await server.stop()
    return len(calls)


def test_round6_k22_server_drains_after_each_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    original = Connection.drained

    def _spy(connection: Connection) -> None:
        calls.append(id(connection))
        original(connection)

    monkeypatch.setattr(Connection, "drained", _spy)
    assert asyncio.run(_drain_calls(tmp_path, calls)) == 2


def test_round6_k22_default_database_honours_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_database() == tmp_path / "aconsole" / "aconsole.db"
    monkeypatch.setenv("XDG_DATA_HOME", "")
    assert default_database() == Path.home() / ".local" / "share" / "aconsole" / "aconsole.db"


def test_round6_k22_prepare_creates_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "data" / "aconsole.db"
    assert prepare_database(path, now=_NOW) is None
    assert path.exists()
    assert not pending_upgrade(path)
    assert _tables(path) >= _TABLES
    assert _backups(path) == []


def test_round6_k22_prepare_at_head_is_a_no_op(tmp_path: Path) -> None:
    path = tmp_path / "aconsole.db"
    assert prepare_database(path, now=_NOW) is None
    assert prepare_database(path, now=_NOW + timedelta(hours=1)) is None
    assert _backups(path) == []


def test_round6_k22_prepare_backs_up_an_empty_older_file(tmp_path: Path) -> None:
    path = tmp_path / "aconsole.db"
    sqlite3.connect(path).close()
    assert pending_upgrade(path)
    saved = prepare_database(path, now=_NOW)
    assert saved is not None
    assert saved.exists()
    assert saved.name.endswith(".bak")
    assert _is_sqlite_file(saved)
    assert not pending_upgrade(path)
    assert _tables(path) >= _TABLES


def test_round6_k22_prepare_backup_keeps_content(tmp_path: Path) -> None:
    path = tmp_path / "aconsole.db"
    engine = write_engine(sqlite_url(path))
    try:
        legacy = MetaData()
        _ = Table("legacy", legacy, Column("id", Integer, primary_key=True)).create(engine)
    finally:
        engine.dispose()
    saved = prepare_database(path, now=_NOW)
    assert saved is not None
    assert "legacy" in _tables(saved)
    assert _tables(path) >= _TABLES


def test_round6_k22_backup_keeps_three(tmp_path: Path) -> None:
    path = tmp_path / "aconsole.db"
    sqlite3.connect(path).close()
    moments = [_NOW + timedelta(minutes=offset) for offset in range(5)]
    names = [backup(path, now=moment) for moment in moments]
    assert _backups(path) == names[-3:]
    assert not names[0].exists()
    assert not names[1].exists()


def _seed(path: Path, now: datetime) -> None:
    assert prepare_database(path, now=now) is None
    engine = write_engine(sqlite_url(path))
    try:
        with Session(engine) as session:
            session.add(
                Project(
                    id=_PROJECT,
                    name="demo",
                    root_path="/home/sas/Projects/demo",
                    languages=["python"],
                    created_at=now,
                )
            )
            session.flush()
            session.add(_tab("tab-old", closed_at=now - timedelta(days=31), now=now))
            session.add(_tab("tab-recent", closed_at=now - timedelta(days=1), now=now))
            session.add(_quota("old", observed_at=now - timedelta(days=31)))
            session.add(_quota("fresh", observed_at=now - timedelta(days=1)))
            session.commit()
    finally:
        engine.dispose()


def _tab(tab_id: str, *, closed_at: datetime, now: datetime) -> Tab:
    return Tab(
        id=tab_id,
        project_id=_PROJECT,
        agent_kind=AgentKind.CLAUDE,
        title=tab_id,
        tab_order=0,
        last_status=TabStatus.IDLE,
        created_at=now,
        updated_at=now,
        closed_at=closed_at,
    )


def _quota(window_key: str, *, observed_at: datetime) -> QuotaHistory:
    return QuotaHistory(
        account_id="claude:demo",
        window_key=window_key,
        used_pct=1.0,
        observed_at=observed_at,
    )


def _read_rows(path: Path) -> tuple[list[str], list[str]]:
    engine = write_engine(sqlite_url(path))
    try:
        with Session(engine) as session:
            tabs = list(session.scalars(select(Tab.id)).all())
            quota = list(session.scalars(select(QuotaHistory.window_key)).all())
    finally:
        engine.dispose()
    return tabs, quota


def test_round6_k22_daemon_creates_database_and_exits(tmp_path: Path) -> None:
    database = tmp_path / "data" / "aconsole.db"
    daemon = Daemon(tmp_path / "aconsole" / "core.sock", idle_s=0.2, database=database)
    assert asyncio.run(daemon.serve()) == 0
    assert _tables(database) >= _TABLES
    assert {"tabs", "events"} <= _tables(database)
    assert isinstance(daemon.dropped_refs, list)


def test_round6_k22_daemon_sweep_runs(tmp_path: Path) -> None:
    database = tmp_path / "data" / "aconsole.db"
    now = datetime.now(UTC)
    _seed(database, now)
    daemon = Daemon(tmp_path / "aconsole" / "core.sock", idle_s=0.5, database=database)
    assert asyncio.run(daemon.serve()) == 0
    tabs, quota = _read_rows(database)
    assert tabs == ["tab-recent"]
    assert quota == ["fresh"]
    assert daemon.dropped_refs == []


def _drift(engine: Engine) -> object:
    """Alembic autogenerate's view of the difference between the database and the ORM metadata."""
    with engine.connect() as connection:
        return _COMPARE(MigrationContext.configure(connection), Base.metadata)


def test_round6_k22_migration_matches_models(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'aconsole.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    try:
        assert _drift(engine) == []
    finally:
        engine.dispose()


def test_round6_k22_downgrade_leaves_no_app_tables(tmp_path: Path) -> None:
    path = tmp_path / "aconsole.db"
    migrate(f"sqlite:///{path}", "head")
    migrate(f"sqlite:///{path}", "base")
    assert _tables(path) == {"alembic_version"}


def test_round6_k22_downgrade_keeps_foreign_table(tmp_path: Path) -> None:
    path = tmp_path / "aconsole.db"
    url = f"sqlite:///{path}"
    migrate(url, "head")
    engine = write_engine(url)
    try:
        foreign = MetaData()
        _ = Table("foreign_t", foreign, Column("id", Integer, primary_key=True)).create(engine)
    finally:
        engine.dispose()
    migrate(url, "base")
    assert _tables(path) == {"alembic_version", "foreign_t"}
