"""Explicit unit of work. Nothing is committed unless ``commit`` is called."""

from __future__ import annotations

from types import TracebackType
from typing import Self, final

from sqlalchemy.orm import Session, sessionmaker


@final
class UnitOfWork:
    """``with uow`` opens a session. The caller commits."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory
        self.session: Session | None = None
        self._committed = False

    def __enter__(self) -> Self:
        self.session = self._factory()
        self._committed = False
        return self

    def commit(self) -> None:
        session = self._require()
        session.commit()
        self._committed = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        session = self.session
        self.session = None
        if session is None:
            return
        if exc_type is not None or not self._committed:
            session.rollback()
        session.close()

    def _require(self) -> Session:
        session = self.session
        if session is None:
            message = "unit of work is closed"
            raise RuntimeError(message)
        return session
