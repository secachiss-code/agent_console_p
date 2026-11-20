"""SQLite FTS5 schema and statements, executed on the caller's transaction."""

from __future__ import annotations

import sqlite3

import msgspec
from sqlalchemy import Connection
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

_MATCH_ERRORS = (
    "fts5: syntax error",
    "fts5: parser stack overflow",
    "no such column: ",
    "unknown special query:",
)
_FTS_TABLES = frozenset(
    {"note_fts", "note_fts_config", "note_fts_content", "note_fts_data", "note_fts_docsize", "note_fts_idx"}
)


def include_orm_schema_name(name: str | None, type_: str, _parent_names: object) -> bool:
    """Leave the migration-owned FTS5 table and its SQLite shadow tables to SQLite."""
    return type_ != "table" or name not in _FTS_TABLES


def create_index(connection: Connection) -> None:
    """Create unicode61 storage and remove entries when their source is deleted."""
    _ = connection.exec_driver_sql(
        "CREATE VIRTUAL TABLE note_fts USING fts5(title, body, note_ref UNINDEXED, tokenize = 'unicode61')"
    )
    _ = connection.exec_driver_sql(
        """CREATE TRIGGER note_fts_delete_kb_nodes AFTER DELETE ON kb_nodes
        BEGIN DELETE FROM note_fts WHERE note_ref = OLD.note_ref; END"""
    )
    _ = connection.exec_driver_sql(
        """CREATE TRIGGER note_fts_delete_vault_files AFTER DELETE ON vault_files
        BEGIN DELETE FROM note_fts WHERE note_ref = OLD.note_ref; END"""
    )


def drop_index(connection: Connection) -> None:
    for table in ("kb_nodes", "vault_files"):
        _ = connection.exec_driver_sql(f"DROP TRIGGER note_fts_delete_{table}")
    _ = connection.exec_driver_sql("DROP TABLE note_fts")


def replace_note(session: Session, note_ref: str, title: str, body: str) -> None:
    """Replace one document without committing its source transaction."""
    connection = session.connection()
    _ = connection.exec_driver_sql("DELETE FROM note_fts WHERE note_ref = ?", (note_ref,))
    _ = connection.exec_driver_sql(
        "INSERT INTO note_fts (title, body, note_ref) VALUES (?, ?, ?)", (title, body, note_ref)
    )


def search_facts(session: Session, project_id: str, query: str, limit: int) -> tuple[list[str], frozenset[str]]:
    """Read ranked matches and the project's unindexed fact IDs in one transaction."""
    connection = session.connection()
    try:
        matches = connection.exec_driver_sql(
            """SELECT k.id FROM note_fts
               JOIN kb_nodes AS k ON k.note_ref = note_fts.note_ref
               WHERE note_fts MATCH ? AND k.project_id = ?
               ORDER BY bm25(note_fts), k.id LIMIT ?""",
            (query, project_id, limit),
        )
        ranked = msgspec.convert(matches.scalars().all(), type=list[str])
    except OperationalError as exc:
        original = exc.orig
        if isinstance(original, sqlite3.OperationalError) and original.sqlite_errorcode == sqlite3.SQLITE_ERROR:
            message = str(original)
            if message == "unterminated string" or message.startswith(_MATCH_ERRORS):
                return [], frozenset()
        raise

    if len(ranked) == limit:
        return ranked, frozenset()
    legacy = connection.exec_driver_sql(
        """SELECT id FROM kb_nodes WHERE project_id = ?
           AND (note_ref IS NULL OR note_ref NOT IN
               (SELECT note_ref FROM note_fts WHERE note_ref IS NOT NULL))""",
        (project_id,),
    )
    return ranked, msgspec.convert(legacy.scalars().all(), type=frozenset[str])
