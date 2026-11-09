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
                root_path="/home/example/Projects/demo",
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


def test_migrations_are_found_in_a_package_and_in_a_checkout(tmp_path: Path) -> None:
    from aconsole.core.infrastructure.persistence.engine import migrations_dir

    module = tmp_path / "site" / "aconsole" / "core" / "infrastructure" / "persistence" / "engine.py"
    module.parent.mkdir(parents=True)
    module.touch()
    assert migrations_dir(module) == tmp_path / "migrations"
    packaged = tmp_path / "site" / "aconsole" / "_migrations"
    packaged.mkdir()
    assert migrations_dir(module) == packaged

def test_wal_pinned_reader_is_not_interrupted_and_compacts_after_release(tmp_path: Path) -> None:
    import time

    from aconsole.core.infrastructure.persistence.engine import checkpoint_wal
    from aconsole.core.infrastructure.persistence.models import Setting

    database = tmp_path / "reader.db"
    url = f"sqlite:///{database}"
    migrate(url, "head")
    engine = write_engine(url)
    reader = sqlite3.connect(database)
    try:
        with Session(engine) as session:
            session.add(Setting(key="before", value={"data": "old"}, rev=1))
            session.commit()
        _ = checkpoint_wal(engine, truncate=True)
        _ = reader.execute("BEGIN")
        assert reader.execute("SELECT count(*) FROM settings").fetchone()[0] == 1
        with Session(engine) as session:
            session.add(Setting(key="after", value={"data": "new"}, rev=1))
            session.commit()
        started = time.monotonic()
        status = checkpoint_wal(engine, truncate=True)
        assert time.monotonic() - started < 1
        assert status.busy
        assert reader.execute("SELECT count(*) FROM settings").fetchone()[0] == 1
        reader.rollback()
        assert not checkpoint_wal(engine, truncate=True).busy
        assert database.with_name(database.name + "-wal").stat().st_size == 0
        with Session(engine) as session:
            assert session.get(Setting, "after") is not None
    finally:
        reader.close()
        engine.dispose()
