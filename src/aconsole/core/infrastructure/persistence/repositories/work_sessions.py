"""Working session records through the application's single database writer."""

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aconsole.core.application.ports.plan_store import StoreWriteError
from aconsole.core.domain.work_session import WorkSession, WorkSessionSnapshot
from aconsole.core.infrastructure.persistence.models import WorkSessionRow, WorkSessionSnapshotRow, Workspace
from aconsole.core.infrastructure.persistence.writer import Writer


class WorkSessionDatabase:
    def __init__(self, writer: Writer) -> None:
        self._writer: Writer = writer

    def load(self) -> tuple[WorkSession, ...]:
        records: list[WorkSession] = []

        def read(session: Session) -> None:
            records.extend(
                WorkSession(
                    id=row.id,
                    project_id=row.project_id,
                    title=row.title,
                    worktree=row.worktree,
                    branch=row.branch,
                    base_branch=row.base_branch,
                    workspace_mode=row.workspace_mode,
                    status="archived" if row.status == "archived" else "active",
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    workspace_removed=row.workspace_removed,
                )
                for row in session.scalars(
                    select(WorkSessionRow).order_by(WorkSessionRow.created_at, WorkSessionRow.id)
                )
            )

        _ = self._writer.call(read)
        return tuple(records)

    def save(self, record: WorkSession) -> None:
        def write(session: Session) -> None:
            _ = session.merge(
                WorkSessionRow(
                    id=record.id,
                    project_id=record.project_id,
                    title=record.title,
                    worktree=record.worktree,
                    branch=record.branch,
                    base_branch=record.base_branch,
                    workspace_mode=record.workspace_mode,
                    status=record.status,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                    workspace_removed=record.workspace_removed,
                )
            )
            if record.workspace_removed:
                for workspace in session.scalars(select(Workspace).where(Workspace.path == record.worktree)):
                    workspace.removed_at = record.updated_at
                    workspace.locked = False

        try:
            _ = self._writer.call(write)
        except (SQLAlchemyError, OSError, TimeoutError) as exc:
            raise StoreWriteError(str(exc)) from exc


class WorkSessionHistoryDatabase:
    def __init__(self, writer: Writer) -> None:
        self._writer: Writer = writer

    def load(self) -> tuple[WorkSessionSnapshot, ...]:
        records: list[WorkSessionSnapshot] = []

        def read(session: Session) -> None:
            records.extend(
                WorkSessionSnapshot(
                    work_session_id=row.work_session_id,
                    sequence=row.sequence,
                    kind=row.kind,
                    actor_tab_id=row.actor_tab_id,
                    worktree=row.worktree,
                    ref=row.ref,
                    head_oid=row.head_oid,
                    oid=row.oid,
                    tree=row.tree,
                    previous_oid=row.previous_oid,
                    label=row.label,
                    status=row.status,
                    created_at=row.created_at,
                    actor_title=row.actor_title,
                    agent=row.agent,
                    plan_node=row.plan_node,
                )
                for row in session.scalars(select(WorkSessionSnapshotRow))
            )

        _ = self._writer.call(read)
        return tuple(records)

    def save(self, record: WorkSessionSnapshot) -> None:
        def write(session: Session) -> None:
            _ = session.merge(
                WorkSessionSnapshotRow(
                    work_session_id=record.work_session_id,
                    sequence=record.sequence,
                    kind=record.kind,
                    actor_tab_id=record.actor_tab_id,
                    worktree=record.worktree,
                    ref=record.ref,
                    head_oid=record.head_oid,
                    oid=record.oid,
                    tree=record.tree,
                    previous_oid=record.previous_oid,
                    label=record.label,
                    status=record.status,
                    created_at=record.created_at,
                    actor_title=record.actor_title,
                    agent=record.agent,
                    plan_node=record.plan_node,
                )
            )

        try:
            _ = self._writer.call(write)
        except (SQLAlchemyError, OSError, TimeoutError) as exc:
            raise StoreWriteError(str(exc)) from exc
