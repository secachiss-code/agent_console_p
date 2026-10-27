"""SQLite engines. String SQL lives only in this module."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool

MIGRATIONS = Path(__file__).resolve().parents[5] / "migrations"
_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",
    "PRAGMA optimize=0x10002",
    "PRAGMA wal_autocheckpoint=4000",
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
