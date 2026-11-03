"""Checkpoint rows through the single writer. The refs themselves live in git."""

from __future__ import annotations

from datetime import UTC
from secrets import token_bytes
from typing import final

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from aconsole.core.domain.checkpoints import Checkpoint
from aconsole.core.domain.clock import Clock
from aconsole.core.domain.ids import new_ulid
from aconsole.core.domain.workspace import path_segment
from aconsole.core.infrastructure.persistence.models import Checkpoint as Row
from aconsole.core.infrastructure.persistence.writer import Writer


@final
class CheckpointDatabase:
    """`CheckpointJournal` on the `checkpoints` table."""

    def __init__(self, writer: Writer) -> None:
        self._writer: Writer = writer

    def load(self, tab_id: str) -> tuple[Checkpoint, ...]:
        found: list[Checkpoint] = []

        def collect(session: Session) -> None:
            rows = session.scalars(select(Row).where(Row.tab_id == tab_id).order_by(Row.turn)).all()
            found.extend(_domain(row) for row in rows)

        _ = self._writer.call(collect)
        return tuple(found)

    def save(self, checkpoint: Checkpoint, tree: str) -> None:
        def add(session: Session) -> None:
            session.add(
                Row(
                    id=new_ulid(Clock(), token_bytes),
                    tab_id=checkpoint.tab_id,
                    turn=checkpoint.number,
                    git_ref=checkpoint.ref,
                    tree_sha=tree,
                    label=checkpoint.label,
                    files_changed=checkpoint.files_changed,
                    insertions=checkpoint.insertions,
                    deletions=checkpoint.deletions,
                    created_at=checkpoint.created_at,
                )
            )

        _ = self._writer.call(add)

    def delete(self, tab_id: str, numbers: tuple[int, ...]) -> None:
        def remove(session: Session) -> None:
            _ = session.execute(delete(Row).where(Row.tab_id == tab_id, Row.turn.in_(numbers)))

        _ = self._writer.call(remove)


def _domain(row: Row) -> Checkpoint:
    at = row.created_at if row.created_at.tzinfo is not None else row.created_at.replace(tzinfo=UTC)
    return Checkpoint(
        id=f"{path_segment(row.tab_id)}-{row.turn}",
        tab_id=row.tab_id,
        number=row.turn,
        created_at=at,
        label=row.label,
        files_changed=row.files_changed,
        insertions=row.insertions,
        deletions=row.deletions,
    )
