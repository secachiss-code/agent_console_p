"""One thread owns the write connection and commits in batches."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future as ThreadFuture
from dataclasses import dataclass, field
from typing import final

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

_BATCH = 500
_WINDOW_S = 0.05
_WAIT_S = 5.0


_LOG = logging.getLogger(__name__)


@dataclass
class _Job:
    work: Callable[[Session], object]
    loop: asyncio.AbstractEventLoop | None
    future: asyncio.Future[object] | None
    blocking: bool = False
    running: bool = False
    completion: ThreadFuture[object] = field(default_factory=ThreadFuture[object])


@final
class Writer:
    """Queue writes. A batch is at most 500 jobs or 50 ms, then one commit."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._queue: queue.Queue[_Job | None] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="aconsole-writer", daemon=True)
        self._started = False
        self._stopping = False
        self._state_lock = threading.Lock()
        self.last_tick_s = 0.0
        self.failed_jobs = 0

    @property
    def queued(self) -> int:
        """Jobs waiting for the writer thread."""
        return self._queue.qsize()

    def start(self) -> None:
        with self._state_lock:
            if self._stopping:
                message = "a stopped writer cannot restart"
                raise RuntimeError(message)
            if self._started:
                return
            self._thread.start()
            self._started = True

    def stop(self) -> None:
        with self._state_lock:
            if not self._started:
                return
            if not self._stopping:
                self._stopping = True
                self._queue.put(None)
        self._thread.join(timeout=_WAIT_S)
        if self._thread.is_alive():
            message = "writer did not stop; accepted writes are still draining"
            raise TimeoutError(message)
        with self._state_lock:
            self._started = False

    def _enqueue(self, job: _Job) -> None:
        # The sentinel and acceptance share this lock, so no accepted job can land after shutdown.
        with self._state_lock:
            if not self._started or self._stopping or not self._thread.is_alive():
                message = "writer is not accepting work"
                raise RuntimeError(message)
            self._queue.put(job)

    def submit(self, work: Callable[[Session], object]) -> asyncio.Future[object]:
        """Run ``work`` on the writer thread; cancellation skips work that has not started."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[object] = loop.create_future()
        job = _Job(work=work, loop=loop, future=future)

        def cancelled(done: asyncio.Future[object]) -> None:
            if done.cancelled():
                _ = job.completion.cancel()

        future.add_done_callback(cancelled)
        self._enqueue(job)
        return future

    def call(self, work: Callable[[Session], object]) -> object:
        """Cancel a queued job on timeout; once started, wait for its definitive commit or failure.

        Returning a timeout while a transaction is still allowed to commit would let callers
        roll back memory even though the database later changes. Running work cannot be cancelled.
        """
        job = _Job(work=work, loop=None, future=None, blocking=True)
        self._enqueue(job)
        try:
            return job.completion.result(timeout=_WAIT_S)
        except TimeoutError:
            if job.completion.cancel():
                message = "writer did not start the job before its deadline"
                raise TimeoutError(message) from None
            return job.completion.result()

    def post(self, work: Callable[[Session], object]) -> None:
        """Queue ``work`` and return at once. Order is kept; a failure is logged, and nobody waits for it."""
        self._enqueue(_Job(work=work, loop=None, future=None))

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
                # Somebody is blocked on this job; the window would only make them wait.
                if item.blocking or len(pending) >= _BATCH or time.monotonic() >= deadline:
                    self._flush(session, pending)
                    pending = []
        finally:
            session.close()

    def _flush(self, session: Session, jobs: list[_Job]) -> None:
        started = time.perf_counter()
        active: list[_Job] = []
        results: list[object] = []
        try:
            for job in jobs:
                if not job.running:
                    if job.completion.cancelled() or not job.completion.set_running_or_notify_cancel():
                        continue
                    job.running = True
                active.append(job)
                results.append(job.work(session))
            if active:
                session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            if len(jobs) > 1:
                # One bad job must not take the rest of the batch with it.
                for job in jobs:
                    self._flush(session, [job])
            else:
                self.failed_jobs += 1
                self._fail(jobs[0], exc)
            self.last_tick_s = time.perf_counter() - started
            return
        self.last_tick_s = time.perf_counter() - started
        for job, result in zip(active, results, strict=True):
            self._succeed(job, result)

    def _succeed(self, job: _Job, result: object) -> None:
        job.completion.set_result(result)
        self._notify(job, result, None)

    def _fail(self, job: _Job, exc: BaseException) -> None:
        job.completion.set_exception(exc)
        if job.loop is None and not job.blocking:
            _LOG.error("a queued write failed: %s", exc)
        self._notify(job, None, exc)

    @staticmethod
    def _notify(job: _Job, result: object, error: BaseException | None) -> None:
        if job.loop is not None and job.future is not None:
            try:
                _ = job.loop.call_soon_threadsafe(_resolve, job.future, result, error)
            except RuntimeError:
                # The transaction is already finished. A vanished caller must not kill the writer.
                _LOG.warning("a write finished after its caller's event loop closed")


def _resolve(future: asyncio.Future[object], result: object, error: BaseException | None) -> None:
    if future.done():
        return
    if error is not None:
        future.set_exception(error)
        return
    future.set_result(result)
