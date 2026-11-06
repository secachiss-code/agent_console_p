"""Durable working sessions, independent of CLI session transcripts."""

from typing import Protocol

from aconsole.core.domain.work_session import WorkSession, WorkSessionSnapshot


class WorkSessionStore(Protocol):
    def load(self) -> tuple[WorkSession, ...]: ...

    def save(self, record: WorkSession) -> None: ...


class WorkSessionHistoryStore(Protocol):
    def load(self) -> tuple[WorkSessionSnapshot, ...]: ...

    def save(self, record: WorkSessionSnapshot) -> None: ...
