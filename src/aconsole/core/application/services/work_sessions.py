"""The book of task sessions. Mutations are serialized by the tab worker."""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from aconsole.contracts.common import ProjectId, WorkspaceMode
from aconsole.contracts.work_sessions import WorkSessionView
from aconsole.core.application.ports.plan_store import StoreWriteError
from aconsole.core.application.ports.work_session_store import WorkSessionStore
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.work_session import WorkSession


def session_view(record: WorkSession) -> WorkSessionView:
    return WorkSessionView(
        id=record.id,
        project_id=ProjectId(record.project_id),
        title=record.title,
        worktree=record.worktree,
        branch=record.branch,
        base_branch=record.base_branch,
        workspace=WorkspaceMode(record.workspace_mode),
        status=record.status,
        created_at=record.created_at,
        updated_at=record.updated_at,
        workspace_removed=record.workspace_removed,
    )


_TITLE_LIMIT = 120


class WorkSessionService:
    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now: Callable[[], datetime] = now
        self._records: dict[str, WorkSession] = {}
        self._store: WorkSessionStore | None = None

    def use_store(self, store: WorkSessionStore) -> None:
        records = store.load()
        self._records = {record.id: record for record in records}
        self._store = store

    def all(self, project_id: str, *, include_archived: bool = False) -> tuple[WorkSession, ...]:
        return tuple(
            record
            for record in tuple(self._records.values())
            if record.project_id == project_id and (include_archived or record.status == "active")
        )

    def require(self, project_id: str, session_id: str, *, active: bool = False) -> WorkSession:
        record = self._records.get(session_id)
        if record is None or record.project_id != project_id:
            raise DomainError(code=3040, name="WORK_SESSION_NOT_FOUND", retryable=False)
        if active and record.status != "active":
            raise DomainError(code=3041, name="WORK_SESSION_ARCHIVED", retryable=False)
        return record

    def at(self, project_id: str, worktree: str) -> WorkSession | None:
        path = str(Path(worktree).resolve())
        return next(
            (row for row in tuple(self._records.values())
             if row.project_id == project_id and row.worktree == path and not row.workspace_removed),
            None,
        )

    def add(self, record: WorkSession) -> WorkSession:
        path = str(Path(record.worktree).resolve())
        if not record.workspace_removed and any(
            row.worktree == path and row.id != record.id and not row.workspace_removed
            for row in tuple(self._records.values())
        ):
            raise DomainError(code=3042, name="WORK_SESSION_WORKSPACE_CONFLICT", retryable=False)
        saved = replace(record, worktree=path)
        if self._store is not None:
            try:
                self._store.save(saved)
            except (StoreWriteError, OSError, TimeoutError) as exc:
                raise DomainError(code=3032, name="DATABASE_TRANSACTION_FAILED", retryable=True) from exc
        self._records[saved.id] = saved
        return saved

    def rename(self, project_id: str, session_id: str, title: str) -> WorkSession:
        text = title.strip()
        if not text or len(text) > _TITLE_LIMIT:
            message = "session title must contain 1 to 120 characters"
            raise ValueError(message)
        return self.add(replace(self.require(project_id, session_id), title=text, updated_at=self._now()))

    def archive(self, project_id: str, session_id: str) -> WorkSession:
        return self.add(replace(self.require(project_id, session_id), status="archived", updated_at=self._now()))

    def resume(self, project_id: str, session_id: str) -> WorkSession:
        if self.require(project_id, session_id).workspace_removed:
            raise DomainError(code=3021, name="GIT_WORKTREE_UNKNOWN", retryable=False)
        return self.add(replace(self.require(project_id, session_id), status="active", updated_at=self._now()))

    def removed(self, project_id: str, session_id: str) -> WorkSession:
        return self.add(replace(
            self.require(project_id, session_id), status="archived", workspace_removed=True, updated_at=self._now(),
        ))
