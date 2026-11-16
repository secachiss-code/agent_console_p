"""SQLite engines. String SQL lives only in this module."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Connection, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool
lazy from alembic import command
lazy from alembic.config import Config

from aconsole.core.infrastructure.persistence.cache.schema import CACHE_SCHEMA


def migrations_dir(module: Path | None = None) -> Path:
    """`aconsole/_migrations` in an installed package, `migrations/` in a checkout."""
    resolved = Path(__file__ if module is None else module).resolve()
    packaged = resolved.parents[3] / "_migrations"
    return packaged if packaged.is_dir() else resolved.parents[5] / "migrations"


MIGRATIONS = migrations_dir()
_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",
    "PRAGMA optimize=0x10002",
    "PRAGMA wal_autocheckpoint=1000",
    "PRAGMA journal_size_limit=4194304",
)
_WRITE_POOL = 1
_READ_POOL = 4


def write_engine(url: str) -> Engine:
    """One connection. Transactions start with ``BEGIN IMMEDIATE``."""
    engine = create_engine(url, pool_size=_WRITE_POOL, max_overflow=0, connect_args={"timeout": 5.0})
    _listen(engine, immediate=True)
    return engine


def read_engine(url: str) -> Engine:
    """A queue of read connections. Each thread keeps its own session."""
    engine = create_engine(
        url,
        poolclass=QueuePool,
        pool_size=_READ_POOL,
        max_overflow=0,
        connect_args={"timeout": 5.0},
    )
    _listen(engine, immediate=False)
    return engine


def apply_pragmas(dbapi_connection: sqlite3.Connection) -> None:
    """WAL, foreign keys, and the checkpoint size. Called on every connect."""
    cursor = dbapi_connection.cursor()
    for pragma in _PRAGMAS:
        _ = cursor.execute(pragma)
    _ = cursor.close()


def begin_immediate(connection: Connection) -> None:
    """Take the write lock at the start of a transaction."""
    _ = connection.exec_driver_sql("BEGIN IMMEDIATE")


def synchronous_full(dbapi_connection: sqlite3.Connection) -> None:
    """Durability for a lease transition. Set this before the transaction begins."""
    cursor = dbapi_connection.cursor()
    _ = cursor.execute("PRAGMA synchronous=FULL")
    _ = cursor.close()


def optimize(connection: Connection) -> None:
    """Run the planner pass the connect hook left at ``optimize=0x10002``."""
    _ = connection.exec_driver_sql("PRAGMA optimize")


def foreign_keys(connection: Connection, *, enabled: bool) -> None:
    """Batch migrations turn foreign keys off, then back on."""
    flag = "ON" if enabled else "OFF"
    _ = connection.exec_driver_sql(f"PRAGMA foreign_keys={flag}")


def migrate(url: str, revision: str) -> None:
    """Upgrade or downgrade to ``revision`` (``head`` or ``base``)."""
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url)
    if revision == "base":
        command.downgrade(cfg, "base")
        return
    command.upgrade(cfg, revision)


def _listen(engine: Engine, *, immediate: bool) -> None:
    event.listen(engine, "connect", _on_connect)
    if immediate:
        event.listen(engine, "begin", _on_begin)


def _on_connect(dbapi_connection: sqlite3.Connection, _record: object) -> None:
    apply_pragmas(dbapi_connection)


def _on_begin(connection: Connection) -> None:
    begin_immediate(connection)



@dataclass(frozen=True)
class WalCheckpoint:
    busy: bool
    pages: int
    checkpointed: int


def checkpoint_wal(engine: Engine, *, truncate: bool = False) -> WalCheckpoint:
    """Outside a transaction; never wait for a reader or interrupt it to shrink WAL."""
    pooled = engine.raw_connection()
    try:
        connection = pooled.driver_connection
        if not isinstance(connection, sqlite3.Connection):
            message = "WAL maintenance requires SQLite"
            raise TypeError(message)
        _ = connection.execute("PRAGMA busy_timeout=0")
        try:
            mode = "TRUNCATE" if truncate else "PASSIVE"
            cursor = connection.execute(f"PRAGMA wal_checkpoint({mode})")
            row: object = cursor.fetchone()  # pyright: ignore[reportAny] -- sqlite3 row is validated below
        finally:
            _ = connection.execute("PRAGMA busy_timeout=5000")
        if row is None:
            return WalCheckpoint(busy=False, pages=0, checkpointed=0)
        match row:
            case (int(busy), int(pages), int(done)):
                return WalCheckpoint(busy=bool(busy), pages=pages, checkpointed=done)
            case _:
                message = "SQLite returned an invalid checkpoint result"
                raise ValueError(message)
    finally:
        pooled.close()


def cache_engine(path: Path | None, *, max_mib: int = 64) -> Engine:
    """Open disposable cache storage, recovering incompatible or corrupt files."""
    minimum_mib, maximum_mib = 16, 256
    if not minimum_mib <= max_mib <= maximum_mib:
        message = "cache size must be between 16 and 256 MiB"
        raise ValueError(message)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.exists():
            valid = False
            try:
                with sqlite3.connect(path) as probe:
                    version: object = probe.execute("PRAGMA user_version").fetchone()  # pyright: ignore[reportAny] -- SQLite row checked below
                    integrity: object = probe.execute("PRAGMA quick_check").fetchone()  # pyright: ignore[reportAny] -- SQLite row checked below
                    valid = version == (CACHE_SCHEMA,) and integrity == ("ok",)
            except sqlite3.DatabaseError:
                valid = False
            if not valid:
                path.unlink()
        path.touch(mode=0o600, exist_ok=True)
    engine = create_engine(
        "sqlite://" if path is None else f"sqlite:///{path}",
        poolclass=QueuePool, pool_size=1, max_overflow=0,
        connect_args={"check_same_thread": False},
    )

    def configure(connection: sqlite3.Connection, _record: object) -> None:
        for pragma in (
            "PRAGMA page_size=4096", "PRAGMA journal_mode=MEMORY", "PRAGMA synchronous=OFF",
            "PRAGMA temp_store=MEMORY", "PRAGMA foreign_keys=ON",
            f"PRAGMA max_page_count={max_mib * 256}", f"PRAGMA user_version={CACHE_SCHEMA}",
        ):
            _ = connection.execute(pragma)

    event.listen(engine, "connect", configure)
    return engine
