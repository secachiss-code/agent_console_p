"""Each native application run releases its own loop and pending cleanup."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


async def _stream(cleaned: list[str]) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await asyncio.sleep(0)
        cleaned.append("generator")


async def _pending(cleaned: list[str]) -> None:
    try:
        _ = await asyncio.Event().wait()
    finally:
        await asyncio.sleep(0)
        cleaned.append("task")


@pytest.mark.parametrize("fails", [False, True])
def test_repeated_runs_close_owned_loops_and_finish_cleanup(monkeypatch: pytest.MonkeyPatch, *, fails: bool) -> None:
    from gi.repository import Gio, GLib

    from aconsole.ui.bootstrap import _TASKS, run_gtk, spawn_task

    before = set(_TASKS)
    loops: list[asyncio.AbstractEventLoop] = []
    cleaned: list[str] = []
    generators: list[AsyncIterator[None]] = []
    context = GLib.MainContext.default()
    unrelated = GLib.timeout_add(60_000, lambda: GLib.SOURCE_CONTINUE)
    foreign = asyncio.new_event_loop()

    foreign_task = foreign.create_task(asyncio.sleep(60_000))
    _TASKS.add(foreign_task)

    async def finish(app: Gio.Application) -> None:
        loops.append(asyncio.get_running_loop())
        generator = _stream(cleaned)
        generators.append(generator)
        await anext(generator)
        # Retain a generator and both tracked and directly created tasks.
        # The completed quit task also needs its done callback.
        _ = spawn_task(_pending(cleaned))
        _ = asyncio.get_running_loop().create_task(_pending(cleaned))
        await asyncio.sleep(0)
        app.quit()

    def activate(app: Gio.Application) -> None:
        app.hold()
        _ = spawn_task(finish(app))

    try:
        for _ in range(3):
            app = Gio.Application(flags=Gio.ApplicationFlags.NON_UNIQUE)
            _ = app.connect("activate", activate)
            def fail_after_run(args: list[str], run: Callable[[list[str]], int] = app.run) -> int:
                _ = run(args)
                message = "run failed"
                raise RuntimeError(message)

            if fails:
                monkeypatch.setattr(app, "run", fail_after_run)
                with pytest.raises(RuntimeError, match="run failed"):
                    _ = run_gtk(app, ["loop-cleanup"])
            else:
                assert run_gtk(app, ["loop-cleanup"]) == 0
            assert all(loop.is_closed() for loop in loops)
            assert before | {foreign_task} == _TASKS
            assert not foreign.is_closed()
            assert not foreign_task.done()
            assert context.find_source_by_id(unrelated) is not None
        assert cleaned.count("task") == 6
        assert cleaned.count("generator") == 3
    finally:
        _TASKS.discard(foreign_task)
        _ = foreign_task.cancel()
        _ = foreign.run_until_complete(asyncio.gather(foreign_task, return_exceptions=True))
        foreign.close()
        _ = GLib.source_remove(unrelated)
