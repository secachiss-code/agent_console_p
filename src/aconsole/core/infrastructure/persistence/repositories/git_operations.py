"""Git operations through the single writer."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Final, final

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aconsole.core.domain.git_operation import GitOperationRecord, MergeMode, OperationKind, OperationStatus
from aconsole.core.infrastructure.persistence.models import GitOperationRow as Row
from aconsole.core.infrastructure.persistence.writer import Writer

_LOG: Final = logging.getLogger(__name__)
_OPEN: Final = ("started", "conflicted", "merged")


_KINDS: Final[dict[str, OperationKind]] = {"merge": "merge", "rebase": "rebase", "cherry-pick": "cherry-pick"}
_STATUSES: Final[dict[str, OperationStatus]] = {
    "started": "started",
    "conflicted": "conflicted",
    "merged": "merged",
    "finished": "finished",
    "failed": "failed",
    "aborted": "aborted",
}
_MODES: Final[dict[str, MergeMode]] = {"fast_forward": "fast_forward", "merge": "merge", "squash": "squash"}


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _record(row: Row) -> GitOperationRecord:
    return GitOperationRecord(
        id=row.id,
        project_id=row.project_id,
        kind=_KINDS[row.kind],
        status=_STATUSES[row.status],
        mode=None if row.mode is None else _MODES[row.mode],
        source=row.source,
        target=row.target,
        source_oid=row.source_oid,
        target_oid=row.target_oid,
        result_oid=row.result_oid,
        expected_tree=row.expected_tree,
        worktree=row.worktree,
        tab_id=row.tab_id,
        plan_node=row.plan_node,
        message=row.message,
        follow_up_pending=row.follow_up_pending,
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
    )


@final
class GitOperationDatabase:
    """`GitOperationJournal` on the `git_operations` table."""

    def __init__(self, writer: Writer) -> None:
        self._writer: Writer = writer

    def save(self, record: GitOperationRecord) -> bool:
        def write(session: Session) -> None:
            _ = session.merge(
                Row(
                    id=record.id,
                    project_id=record.project_id,
                    kind=record.kind,
                    status=record.status,
                    mode=record.mode,
                    source=record.source,
                    target=record.target,
                    source_oid=record.source_oid,
                    target_oid=record.target_oid,
                    result_oid=record.result_oid,
                    expected_tree=record.expected_tree,
                    worktree=record.worktree,
                    tab_id=record.tab_id,
                    plan_node=record.plan_node,
                    message=record.message,
                    follow_up_pending=record.follow_up_pending,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
            )

        try:
            _ = self._writer.call(write)
        except (SQLAlchemyError, TimeoutError, OSError) as exc:
            _LOG.warning("git operation %s was not written: %s", record.id, exc)
            return False
        return True

    def find(self, operation_id: str) -> GitOperationRecord | None:
        found: list[GitOperationRecord] = []

        def read(session: Session) -> None:
            row = session.get(Row, operation_id)
            if row is not None:
                found.append(_record(row))

        _ = self._writer.call(read)
        return found[0] if found else None

    def unfinished(self) -> tuple[GitOperationRecord, ...]:
        found: list[GitOperationRecord] = []

        def read(session: Session) -> None:
            rows = session.scalars(select(Row).where(or_(Row.status.in_(_OPEN), Row.follow_up_pending))).all()
            found.extend(_record(row) for row in rows)

        _ = self._writer.call(read)
        return tuple(found)
