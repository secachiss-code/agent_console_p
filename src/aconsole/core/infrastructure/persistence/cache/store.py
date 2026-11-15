"""Locked Core operations, monotonic scratch fences and bounded LRU eviction."""

import sqlite3
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from threading import Lock
from typing import final

from sqlalchemy import Engine, RowMapping, Select, Table, and_, delete, or_, select, tuple_, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import DBAPIError

from aconsole.core.infrastructure.paths import runtime_cache_path
from aconsole.core.infrastructure.persistence.cache.schema import (
    ast_skeleton_cache,
    metadata,
    node_scratchpad,
    verification_gate_cache,
)
from aconsole.core.infrastructure.persistence.engine import cache_engine

Value = str | int | None


class StaleScratchFenceError(Exception):
    """The cached lease is newer, or this fence belongs to another tab."""


@final
class CacheStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._lock = Lock()
        with self._engine.begin() as connection:
            metadata.create_all(connection)

    @classmethod
    def open_runtime(cls, *, max_mib: int = 64) -> CacheStore:
        return cls(cache_engine(runtime_cache_path(), max_mib=max_mib))

    @classmethod
    def open(cls, path: Path | None, *, max_mib: int = 64) -> CacheStore:
        return cls(cache_engine(path, max_mib=max_mib))

    def close(self) -> None:
        with self._lock:
            self._engine.dispose()

    def put(self, table: Table, rows: Sequence[Mapping[str, Value]]) -> None:
        """Batch one transaction; retry SQLITE_FULL once after discarding old derived data."""
        if table not in metadata.tables.values():
            message = "unknown cache table"
            raise ValueError(message)
        if not rows:
            return
        statement = insert(table)
        updates = {column.name: statement.excluded[column.name] for column in table.c if not column.primary_key}
        condition = None
        if table is node_scratchpad:
            condition = or_(
                statement.excluded.fence > table.c.fence,
                and_(statement.excluded.fence == table.c.fence, statement.excluded.tab_id == table.c.tab_id),
            )
        upsert = statement.on_conflict_do_update(
            index_elements=list(table.primary_key.columns), set_=updates, where=condition,
        )
        with self._lock:
            try:
                with self._engine.begin() as connection:
                    result = connection.execute(upsert, list(rows))
                    if table is node_scratchpad and result.rowcount != len(rows):
                        message = "LEASE_STALE_FENCE"
                        raise StaleScratchFenceError(message) from None
            except DBAPIError as exc:
                if not isinstance(exc.orig, sqlite3.Error) or exc.orig.sqlite_errorcode != sqlite3.SQLITE_FULL:
                    raise
                self._evict()
                with self._engine.begin() as connection:
                    result = connection.execute(upsert, list(rows))
                    if table is node_scratchpad and result.rowcount != len(rows):
                        message = "LEASE_STALE_FENCE"
                        raise StaleScratchFenceError(message) from None

    def get(self, table: Table, keys: Mapping[str, Value]) -> RowMapping | None:
        with self._lock, self._engine.begin() as connection:
            predicate = tuple(table.c[key] == value for key, value in keys.items())
            row = connection.execute(select(table).where(*predicate)).mappings().first()
            if row is not None and table is ast_skeleton_cache:
                _ = connection.execute(update(table).where(*predicate).values(last_used_at=time.time_ns() // 1_000_000))
            return row

    def _evict(self) -> None:
        with self._engine.begin() as connection:
            for table, timestamp in (
                (ast_skeleton_cache, ast_skeleton_cache.c.last_used_at),
                (verification_gate_cache, verification_gate_cache.c.cached_at),
            ):
                # Delete up to 256 oldest entries; scratch and session tracking are never eviction victims.
                primary = list(table.primary_key.columns)
                oldest: Select[object, object] = select(primary[0], primary[1]).order_by(timestamp).limit(256)
                _ = connection.execute(delete(table).where(tuple_(*primary).in_(oldest)))
