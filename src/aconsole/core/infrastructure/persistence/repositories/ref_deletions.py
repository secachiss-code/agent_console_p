"""A durable deletion request is acknowledged only after Git succeeds."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import final

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from aconsole.core.infrastructure.persistence.models import Checkpoint, GitRefDeletion, WorkSessionSnapshotRow
from aconsole.core.infrastructure.persistence.writer import Writer


@dataclass(frozen=True)
class RefDeletion:
    repo: Path
    ref: str


@final
class RefDeletionQueue:
    def __init__(self, writer: Writer) -> None:
        self._writer = writer

    def pending(self, limit: int = 16) -> tuple[RefDeletion, ...]:
        jobs: list[RefDeletion] = []

        def collect(session: Session) -> None:
            rows = session.scalars(select(GitRefDeletion).order_by(
                GitRefDeletion.repo_path, GitRefDeletion.git_ref,
            ).limit(limit)).all()
            jobs.extend(RefDeletion(Path(row.repo_path), row.git_ref) for row in rows)

        _ = self._writer.call(collect)
        return tuple(jobs)

    def protected(self, job: RefDeletion) -> bool:
        def check(session: Session) -> object:
            snapshot = session.scalar(select(WorkSessionSnapshotRow.ref).where(
                WorkSessionSnapshotRow.ref == job.ref,
            ).limit(1))
            checkpoint = session.scalar(select(Checkpoint.git_ref).where(Checkpoint.git_ref == job.ref).limit(1))
            return snapshot is not None or checkpoint is not None

        return self._writer.call(check) is True

    def complete(self, job: RefDeletion) -> None:
        def remove(session: Session) -> None:
            _ = session.execute(delete(GitRefDeletion).where(
                GitRefDeletion.repo_path == str(job.repo), GitRefDeletion.git_ref == job.ref,
            ))

        _ = self._writer.call(remove)
