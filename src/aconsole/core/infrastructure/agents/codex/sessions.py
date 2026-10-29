"""Codex session metadata from ``state_5.sqlite``, opened read-only.

SQLAlchemy reflects ``threads``. The rollout file is not opened, so the conversation
body never becomes a title or a preview. Resume is ``codex resume <id>``; fork is
``codex fork <id>``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from urllib.parse import quote

from sqlalchemy import Column, MetaData, String, Table, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from aconsole.contracts.app import SessionInfo, SessionListResult

_ID: Final = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TITLE: Final = 200


def resume_argv(session_id: str | None, *, fork: bool) -> tuple[str, ...]:
    """``codex resume <id>`` or ``codex fork <id>``. A missing or unsafe id is plain ``codex``."""
    if session_id is None or _ID.fullmatch(session_id) is None:
        return ("codex",)
    verb = "fork" if fork else "resume"
    return ("codex", verb, session_id)


def list_sessions(database: Path, project_root: str) -> SessionListResult:
    """Sessions whose cwd is ``project_root``. A missing database is an empty list."""
    if database.is_symlink() or not database.is_file():
        return SessionListResult(sessions=())
    try:
        found = _read(database, _root(project_root))
    except (OSError, SQLAlchemyError):
        return SessionListResult(sessions=())
    found.sort(key=_order, reverse=True)
    return SessionListResult(sessions=tuple(found))


def readonly_engine(database: Path) -> Engine:
    """SQLite with ``mode=ro``. A write through this engine fails."""
    return create_engine(_url(database), poolclass=NullPool)


def _read(database: Path, project_root: str) -> list[SessionInfo]:
    engine = readonly_engine(database)
    try:
        columns = _threads(engine)
        when = datetime.fromtimestamp(database.stat().st_mtime, tz=UTC)
        found: list[SessionInfo] = []
        with engine.connect() as connection:
            for row in connection.execute(select(*columns)):
                info = _session(row, when, project_root)
                if info is not None:
                    found.append(info)
        return found
    finally:
        engine.dispose()


def _threads(engine: Engine) -> tuple[Column[str], ...]:
    """Reflect ``threads``. The declared string columns keep the row typed."""
    metadata = MetaData()
    columns = (
        Column("id", String, primary_key=True),
        Column("cwd", String),
        Column("title", String),
        Column("git_branch", String),
        Column("name", String),
    )
    _ = Table("threads", metadata, *columns, autoload_with=engine)
    return columns


def _session(row: object, when: datetime, project_root: str) -> SessionInfo | None:
    session_id = _at(row, 0)
    if session_id is None or _ID.fullmatch(session_id) is None:
        return None
    cwd = _at(row, 1) or ""
    if _root(cwd) != project_root:
        return None
    title_text = _at(row, 2) or _at(row, 4) or session_id
    preview_source = _at(row, 2) or _at(row, 4)
    preview = None if preview_source is None else _first(preview_source)[:_TITLE]
    return SessionInfo(
        session_id=session_id,
        title=_first(title_text)[:_TITLE],
        started_at=when,
        updated_at=when,
        cwd=cwd,
        branch=_at(row, 3),
        preview=preview,
    )


def _at(row: object, index: int) -> str | None:
    if isinstance(row, (str, bytes)) or not isinstance(row, Sequence) or index >= len(row):
        return None
    value = row[index]
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _order(info: SessionInfo) -> tuple[datetime, str]:
    return info.updated_at, info.session_id


def _root(path: str) -> str:
    return path.rstrip("/") or "/"


def _first(text: str) -> str:
    return text.split("\n", 1)[0].strip()


def _url(database: Path) -> str:
    quoted = quote(database.resolve().as_posix(), safe="/")
    return f"sqlite:///file:{quoted}?mode=ro&uri=true"
