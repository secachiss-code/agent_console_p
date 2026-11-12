"""Scheduling failure must release work that no task took ownership of."""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING

import pytest

from aconsole.ui.bootstrap import _TASKS, spawn_task

if TYPE_CHECKING:
    from collections.abc import Coroutine


def test_spawn_without_running_loop_closes_work() -> None:
    async def work() -> None:
        pytest.fail("unscheduled work ran")

    pending = work()
    try:
        with pytest.raises(RuntimeError, match="no running event loop"):
            _ = spawn_task(pending)
        assert inspect.getcoroutinestate(pending) == inspect.CORO_CLOSED
    finally:
        pending.close()


def test_spawn_factory_failure_closes_work() -> None:
    failure = RuntimeError("task factory rejected work")

    async def work() -> None:
        pytest.fail("rejected work ran")

    def reject(
        _loop: asyncio.AbstractEventLoop, _work: Coroutine[object, object, None], **_kwargs: object,
    ) -> asyncio.Task[None]:
        raise failure

    async def check() -> None:
        loop = asyncio.get_running_loop()
        previous = loop.get_task_factory()
        pending = work()
        before = set(_TASKS)
        loop.set_task_factory(reject)
        try:
            with pytest.raises(RuntimeError) as caught:
                _ = spawn_task(pending)
            assert caught.value is failure
            assert inspect.getcoroutinestate(pending) == inspect.CORO_CLOSED
            assert before == _TASKS
        finally:
            loop.set_task_factory(previous)
            pending.close()

    asyncio.run(check())


@pytest.mark.parametrize("cancel", [False, True])
def test_spawn_retains_and_releases_task(*, cancel: bool) -> None:
    async def check() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        finished: list[bool] = []

        async def work() -> None:
            started.set()
            try:
                await release.wait()
            finally:
                finished.append(True)

        task = spawn_task(work())
        assert task in _TASKS
        await started.wait()
        if cancel:
            _ = task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            await task
        await asyncio.sleep(0)
        assert finished == [True]
        assert task not in _TASKS

    asyncio.run(check())
