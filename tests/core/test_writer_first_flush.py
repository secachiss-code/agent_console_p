"""The first job on an empty writer queue is committed within 60 ms."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from aconsole.core.infrastructure.persistence.writer import Writer

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

_REPEATS = 20
_LIMIT_S = 0.060


def _elapsed(writer: Writer) -> float:
    started = time.perf_counter()

    def _work(_session: Session) -> None:
        return None

    _ = writer.call(_work)
    return time.perf_counter() - started


def test_writer_first_flush(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'store.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    writer = Writer(engine)
    writer.start()
    try:
        samples = [_elapsed(writer) for _ in range(_REPEATS)]
    finally:
        writer.stop()
        engine.dispose()
    assert max(samples) <= _LIMIT_S
