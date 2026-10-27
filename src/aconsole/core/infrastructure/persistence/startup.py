"""Database startup from DATA_MODEL §5: back up the file, then upgrade it to head before any pool opens."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from aconsole.core.infrastructure.persistence.engine import MIGRATIONS, migrate

_DIR_MODE = 0o700
_KEEP_BACKUPS = 3


def default_database() -> Path:
    """`$XDG_DATA_HOME/aconsole/aconsole.db`, `~/.local/share/aconsole/aconsole.db` without it."""
    data = os.environ.get("XDG_DATA_HOME", "")
    root = Path(data) if data else Path.home() / ".local" / "share"
    return root / "aconsole" / "aconsole.db"


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"


def pending_upgrade(path: Path) -> bool:
    """True when the file is missing or its revision is not the head revision."""
    if not path.exists():
        return True
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    head = ScriptDirectory.from_config(config).get_current_head()
    engine = create_engine(sqlite_url(path), poolclass=NullPool)
    try:
        with engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()
    return current != head


def backup(path: Path, *, now: datetime) -> Path:
    """Copy the database with the sqlite3 backup API (consistent under WAL) and keep the newest copies."""
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = path.with_name(f"{path.name}.{stamp}.bak")
    source = sqlite3.connect(path)
    try:
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()
    target.chmod(0o600)
    old = sorted(path.parent.glob(f"{path.name}.*.bak"))
    for stale in old[:-_KEEP_BACKUPS]:
        stale.unlink(missing_ok=True)
    return target


def prepare_database(path: Path, *, now: datetime) -> Path | None:
    """Upgrade `path` to head. Returns the backup taken before the upgrade, or None when none was needed.

    A new file needs no backup. A file already at head is left alone.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(_DIR_MODE)
    if not pending_upgrade(path):
        return None
    saved = backup(path, now=now) if path.exists() else None
    migrate(sqlite_url(path), "head")
    return saved
