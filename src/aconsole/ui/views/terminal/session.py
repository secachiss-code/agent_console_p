"""Spawn and monitor one VTE child without owning its window."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import gi

gi.require_version("Vte", "3.91")
from gi.repository import Gio, GLib, Vte  # noqa: E402

from aconsole.ui.bootstrap import spawn_task  # noqa: E402

if TYPE_CHECKING:
    from aconsole.contracts.app import TerminalLaunchSpec
    from aconsole.ui.viewmodels.terminal_vm import TerminalViewModel


class TerminalSession:
    """Own one spawn operation and retain its lifecycle-reporting worker."""

    def __init__(self, terminal: Vte.Terminal, view_model: TerminalViewModel) -> None:
        self._terminal: Vte.Terminal = terminal
        self._view_model: TerminalViewModel = view_model
        self._phase: str = "new"
        self._worker: asyncio.Task[None] | None = None
        self._pid: asyncio.Future[int] | None = None
        self._started: asyncio.Future[Exception | None] | None = None
        self._finished: asyncio.Future[tuple[Exception | None, Exception | None]] | None = None
        self._cancellable: Gio.Cancellable | None = None

    async def start(self) -> int:
        if self._worker is None:
            loop = asyncio.get_running_loop()
            self._pid = loop.create_future()
            self._started = loop.create_future()
            self._finished = loop.create_future()
            self._phase = "spec"
            self._worker = spawn_task(self._run())
            self._worker.add_done_callback(self._worker_done)
        if self._pid is None:
            message = "terminal startup was not initialized"
            raise RuntimeError(message)
        return await asyncio.shield(self._pid)

    def _worker_done(self, worker: asyncio.Task[None]) -> None:
        if worker.cancelled():
            self._phase = "done"
            for result in (self._pid, self._started, self._finished):
                if result is not None and not result.done():
                    _ = result.cancel()

    async def started_report(self) -> Exception | None:
        if self._started is None:
            message = "terminal has not been started"
            raise RuntimeError(message)
        return await asyncio.shield(self._started)

    async def wait_finished(self) -> tuple[Exception | None, Exception | None]:
        if self._finished is None:
            message = "terminal has not been started"
            raise RuntimeError(message)
        return await asyncio.shield(self._finished)

    def cancel_pending_spawn(self) -> bool:
        if self._phase == "spec" and self._worker is not None:
            self._phase = "cancelling"
            _ = self._worker.cancel()
            return True
        if self._phase == "spawn" and self._cancellable is not None:
            self._phase = "cancelling"
            self._cancellable.cancel()
            return True
        return False

    async def _run(self) -> None:
        pid_result = self._pid
        started_result = self._started
        finished_result = self._finished
        if pid_result is None or started_result is None or finished_result is None:
            message = "terminal startup was not initialized"
            raise RuntimeError(message)
        try:
            spec = await self._view_model.launch_spec()
            loop = asyncio.get_running_loop()
            exit_status: asyncio.Future[int] = loop.create_future()
            spawn_result: asyncio.Future[int] = loop.create_future()
            self._spawn(spec, exit_status, spawn_result)
            pid = await spawn_result
            self._phase = "running"
            pid_result.set_result(pid)
            result = await self._view_model.report_lifecycle(pid, exit_status, started_report_result=started_result)
            finished_result.set_result(result)
        except asyncio.CancelledError:
            if not pid_result.done():
                _ = pid_result.cancel()
            if not started_result.done():
                _ = started_result.cancel()
            if not finished_result.done():
                _ = finished_result.cancel()
            raise
        except Exception as error:  # noqa: BLE001 -- expose failures to the caller and terminate the worker
            if not pid_result.done():
                pid_result.set_exception(error)
                _ = started_result.cancel()
                _ = finished_result.cancel()
            elif not finished_result.done():
                finished_result.set_exception(error)
        finally:
            self._phase = "done"

    def _spawn(
        self,
        spec: TerminalLaunchSpec,
        exit_status: asyncio.Future[int],
        spawn_result: asyncio.Future[int],
    ) -> None:
        def child_exited(_terminal: Vte.Terminal, status: int) -> None:
            if not exit_status.done():
                exit_status.set_result(status)

        def spawned(_terminal: Vte.Terminal, pid: int, error: GLib.Error | None, _data: object) -> None:
            if spawn_result.done():
                return
            if error is not None:
                spawn_result.set_exception(error)
            elif pid <= 0:
                spawn_result.set_exception(RuntimeError("terminal spawn returned an invalid pid"))
            else:
                spawn_result.set_result(pid)

        _ = self._terminal.connect("child-exited", child_exited)
        self._terminal.set_audible_bell(False)
        self._terminal.set_scrollback_lines(spec.scrollback_lines)
        self._cancellable = Gio.Cancellable()
        self._phase = "spawn"
        self._terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            spec.cwd,
            list(spec.argv),
            [f"{key}={value}" for key, value in spec.env.items()],
            GLib.SpawnFlags(Vte.SPAWN_NO_PARENT_ENVV),
            None,
            None,  # pyright: ignore[reportArgumentType] -- installed VTE requires child_setup_data
            -1,  # pyright: ignore[reportArgumentType] -- installed VTE places timeout after child_setup_data
            self._cancellable,  # pyright: ignore[reportArgumentType] -- installed VTE callback follows cancellable
            spawned,
            None,
        )
