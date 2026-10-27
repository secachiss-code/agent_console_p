"""Read sessions stay on the thread that opened them."""

from __future__ import annotations

import threading
from typing import final

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


@final
class Readers:
    """One ``Session`` per thread, on the read engine."""

    def __init__(self, engine: Engine) -> None:
        self._factory = sessionmaker(bind=engine, expire_on_commit=False)
        self._local = threading.local()

    def session(self) -> Session:
        current: Session | None = getattr(self._local, "session", None)
        if current is None:
            current = self._factory()
            self._local.session = current
        return current

    def close(self) -> None:
        current: Session | None = getattr(self._local, "session", None)
        if current is not None:
            current.close()
            self._local.session = None
