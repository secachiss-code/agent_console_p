"""SQLite FTS5 schema and statements, executed on the caller's transaction."""

from __future__ import annotations

from sqlalchemy import Connection
from sqlalchemy.orm import Session


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
