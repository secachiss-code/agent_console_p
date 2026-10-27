"""Migrations up and down, and the two SQLite engines."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from aconsole.core.infrastructure.persistence.engine import (
    migrate,
    optimize,
    read_engine,
    synchronous_full,
    write_engine,
)
from aconsole.core.infrastructure.persistence.models import Base, Project
from aconsole.core.infrastructure.persistence.types import UtcDateTime

_TABLES = frozenset(Base.metadata.tables)


def _names(url: str) -> set[str]:
    with write_engine(url).connect() as connection:
        rows = connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row[0]) for row in rows}


def test_persistence_a_migrations_go_up_and_down(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'aconsole.db'}"
    migrate(url, "head")
    present = _names(url)
    assert present >= _TABLES
    migrate(url, "base")
    assert _TABLES.isdisjoint(_names(url))
    migrate(url, "head")
    assert _names(url) >= _TABLES


def test_persistence_a_round_trip_and_pragmas(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'aconsole.db'}"
    migrate(url, "head")
    moment = datetime(2026, 10, 25, tzinfo=UTC)
    writer = write_engine(url)
    with Session(writer) as session:
        session.add(
            Project(
                id="01JB2Z00000000000000000001",
                name="demo",
                root_path="/home/sas/Projects/demo",
                languages=["python"],
                created_at=moment,
            )
        )
        session.commit()
    reader = read_engine(url)
    with Session(reader) as session:
        found = session.scalars(select(Project)).one()
        assert found.languages == ["python"]
        assert found.created_at == moment
        assert found.default_branch == "main"
        assert found.vault_folder is None
    with writer.connect() as connection:
        mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar()
        assert mode == "wal"
        optimize(connection)
    raw = writer.raw_connection()
    dbapi = raw.driver_connection
    assert isinstance(dbapi, sqlite3.Connection)
    synchronous_full(dbapi)
    raw.close()
    naive = datetime.now(UTC).replace(tzinfo=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        UtcDateTime().process_bind_param(naive, writer.dialect)
