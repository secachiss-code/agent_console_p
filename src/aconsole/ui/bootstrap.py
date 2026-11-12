"""Start a GTK application on the loop that was verified on this machine.

Verified with spikes/s2_ui/probe_loop.py on PyGObject 3.58.1:
``with gi.events.GLibEventLoop() as loop`` then ``app.run()``, and
``app.create_asyncio_task`` from ``activate``.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Coroutine, Sequence
from typing import Protocol, runtime_checkable

from gi.events import GLibEventLoop
from gi.repository import Gio


@runtime_checkable
class _GtkLoop(Protocol):
    """Runtime ``GLibEventLoop`` is a context manager. The stub omits ``__enter__``."""

    def __enter__(self) -> None: ...
    def __exit__(self, exc_type: object, exc: object, tb: object) -> None: ...

_TASKS: set[asyncio.Task[None]] = set()


def spawn_task(work: Coroutine[object, object, None]) -> asyncio.Task[None]:
    """Take ownership of ``work`` and keep its task alive until it finishes."""
    try:
        task = asyncio.get_running_loop().create_task(work)
    except BaseException:
        # No task owns the coroutine when loop lookup or task creation fails.
        # Release its frame even when scheduling is interrupted during shutdown.
        work.close()
        raise
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


def run_app(create_app: Callable[[], Gio.Application], argv: Sequence[str] | None = None) -> int:
    """Build the application and run it on the GTK loop."""
    return run_gtk(create_app(), argv)


def run_gtk(app: Gio.Application, argv: Sequence[str] | None = None) -> int:
    """Run ``app`` inside a ``GLibEventLoop`` context."""
    args = list(argv) if argv is not None else list(sys.argv)
    # GLibEventLoop.__exit__ only unregisters its context. Runner owns the
    # actual shutdown: cancel/await this loop's tasks, finalize async generators
    # and its executor, then close it, including when app.run raises.
    with asyncio.Runner(loop_factory=GLibEventLoop) as runner:
        loop = runner.get_loop()
        if not isinstance(loop, _GtkLoop):
            message = "GLibEventLoop cannot enter a context"
            raise TypeError(message)
        with loop:
            status = app.run(args)
    return int(status)
