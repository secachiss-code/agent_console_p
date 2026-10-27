"""One thread owns the write connection and commits in batches."""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import final

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

_BATCH = 500
_WINDOW_S = 0.05
_WAIT_S = 5.0


@dataclass
class _Job:
    work: Callable[[Session], object]
    loop: asyncio.AbstractEventLoop | None
    future: asyncio.Future[object] | None
    done: threading.Event | None
    result: object = None
    error: BaseException | None = None


@final
class Writer:
    """Queue writes. A batch is at most 500 jobs or 50 ms, then one commit."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._queue: queue.Queue[_Job | None] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="aconsole-writer", daemon=True)
        self._started = False
        self.last_tick_s = 0.0

    def start(self) -> None:
        if self._started:
            return
        self._thread.start()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self._queue.put(None)
        self._thread.join(timeout=_WAIT_S)
        self._started = False

    def submit(self, work: Callable[[Session], object]) -> asyncio.Future[object]:
        """Run ``work`` on the writer thread and finish the future on the caller's loop."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[object] = loop.create_future()
        self._queue.put(_Job(work=work, loop=loop, future=future, done=None))
        return future

    def call(self, work: Callable[[Session], object]) -> object:
        """Blocking submit for tests that are not on an event loop."""
        done = threading.Event()
        job = _Job(work=work, loop=None, future=None, done=done)
        self._queue.put(job)
        if not done.wait(_WAIT_S):
            message = "writer did not finish"
            raise TimeoutError(message)
        if job.error is not None:
            raise job.error
        return job.result

    def _loop(self) -> None:
        session = Session(self._engine)
        pending: list[_Job] = []
        # The window runs from the first job of a batch, so a steady stream cannot postpone the commit.
        deadline = 0.0
        try:
            while True:
                timeout = max(0.0, deadline - time.monotonic()) if pending else None
                try:
                    item = self._queue.get(timeout=timeout)
                except queue.Empty:
                    self._flush(session, pending)
                    pending = []
                    continue
                if item is None:
                    if pending:
                        self._flush(session, pending)
                    return
                if not pending:
                    deadline = time.monotonic() + _WINDOW_S
                pending.append(item)
                if len(pending) >= _BATCH or time.monotonic() >= deadline:
                    self._flush(session, pending)
                    pending = []
        finally:
            session.close()

    def _flush(self, session: Session, jobs: list[_Job]) -> None:
        started = time.perf_counter()
        try:
            results = [job.work(session) for job in jobs]
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            for job in jobs:
                self._fail(job, exc)
            self.last_tick_s = time.perf_counter() - started
            return
        self.last_tick_s = time.perf_counter() - started
        for job, result in zip(jobs, results, strict=True):
            self._succeed(job, result)

    def _succeed(self, job: _Job, result: object) -> None:
        if job.loop is not None and job.future is not None:
            _ = job.loop.call_soon_threadsafe(_resolve, job.future, result, None)
            return
        job.result = result
        if job.done is not None:
            job.done.set()

    def _fail(self, job: _Job, exc: BaseException) -> None:
        if job.loop is not None and job.future is not None:
            _ = job.loop.call_soon_threadsafe(_resolve, job.future, None, exc)
            return
        job.error = exc
        if job.done is not None:
            job.done.set()


def _resolve(future: asyncio.Future[object], result: object, error: BaseException | None) -> None:
    if future.done():
        return
    if error is not None:
        future.set_exception(error)
        return
    future.set_result(result)
