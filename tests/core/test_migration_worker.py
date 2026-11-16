"""A real migration child must preserve recovery and fail before pools open."""

import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
lazy from pathlib import Path

import pytest

from aconsole.core.infrastructure.persistence.startup import prepare_database_isolated

_NOW = datetime(2026, 11, 16, tzinfo=UTC)


def test_worker_creates_database_and_reopens_without_backup(tmp_path: Path) -> None:
    database = tmp_path / "new/data.db"
    assert prepare_database_isolated(database, now=_NOW) is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("select version_num from alembic_version").fetchone() is not None
    assert prepare_database_isolated(database, now=_NOW) is None
    assert not list(database.parent.glob("*.bak"))


def test_worker_preserves_legacy_data_in_backup(tmp_path: Path) -> None:
    database = tmp_path / "old.db"
    with sqlite3.connect(database) as connection:
        connection.execute("create table legacy (value text)")
        connection.execute("insert into legacy values ('kept')")
    saved = prepare_database_isolated(database, now=_NOW)
    assert saved is not None
    with sqlite3.connect(saved) as connection:
        assert connection.execute("select value from legacy").fetchall() == [("kept",)]
    with sqlite3.connect(database) as connection:
        assert connection.execute("select value from legacy").fetchall() == [("kept",)]


def test_worker_rejects_unknown_revision(tmp_path: Path) -> None:
    database = tmp_path / "future.db"
    with sqlite3.connect(database) as connection:
        connection.execute("create table alembic_version (version_num text primary key)")
        connection.execute("insert into alembic_version values ('unknown-future')")
    with pytest.raises(subprocess.CalledProcessError):
        _ = prepare_database_isolated(database, now=_NOW)
    with sqlite3.connect(database) as connection:
        assert connection.execute("select version_num from alembic_version").fetchall() == [("unknown-future",)]


def test_worker_uses_parent_package_outside_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", "absent-relative-checkout")
    assert prepare_database_isolated(tmp_path / "other-cwd.db", now=_NOW) is None


def test_migrations_do_not_reside_in_parent_interpreter(tmp_path: Path) -> None:
    # A fresh parent matters: other database tests deliberately import Alembic.
    program = (
        "import sys; from pathlib import Path; from datetime import UTC,datetime; "
        "from aconsole.core.infrastructure.persistence.startup import prepare_database_isolated; "
        "prepare_database_isolated(Path(sys.argv[1]),now=datetime.now(UTC)); "
        "assert not any(n == 'alembic' or n.startswith(('alembic.', 'mako.', 'pygments.')) for n in sys.modules)"
    )
    subprocess.run([sys.executable, "-c", program, str(tmp_path / "isolated.db")],
                   check=True, env=os.environ.copy(), timeout=30)
