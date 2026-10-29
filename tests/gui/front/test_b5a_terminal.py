"""B5a VTE spawn adapter tests without a real terminal process or window."""

from __future__ import annotations

import asyncio
import importlib
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import pytest

from aconsole.contracts.app import TerminalLaunchSpec

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_TIMEOUT = 1.0

@runtime_checkable
class _SpawnCallback(Protocol):
    def __call__(
        self, terminal: object, pid: int, error: Exception | None, user_data: object
    ) -> None: ...


async def _bounded[T](awaitable: Awaitable[T]) -> T:
    """Make every test wait fail promptly if the adapter stops progressing."""
    return await asyncio.wait_for(awaitable, timeout=_TIMEOUT)


class _FakeTerminal:
    def __init__(self) -> None:
        self.handlers: dict[str, Callable[..., None]] = {}
        self.spawn_args: tuple[object, ...] | None = None
        self.spawn_callback: _SpawnCallback | None = None
        self.spawn_user_data: object = None
        self.spawn_called = asyncio.Event()
        self.audible_bell: bool | None = None
        self.scrollback_lines: int | None = None

    def set_audible_bell(self, enabled: bool) -> None:  # noqa: FBT001 -- Mirrors the VTE terminal API.
        self.audible_bell = enabled

    def set_scrollback_lines(self, lines: int) -> None:
        self.scrollback_lines = lines

    def connect(self, signal: str, callback: Callable[..., None]) -> int:
        self.handlers[signal] = callback
        return len(self.handlers)

    def spawn_async(self, *args: object) -> None:
        self.spawn_args = args
        # VTE's spawn_async arguments end with callback, user_data.
        callback = args[-2]
        assert isinstance(callback, _SpawnCallback)
        self.spawn_callback = callback
        self.spawn_user_data = args[-1]
        self.spawn_called.set()

    def complete_spawn(self, pid: int, error: Exception | None = None) -> None:
        assert self.spawn_callback is not None
        # VTE calls back with (terminal, pid, error, user_data). A failed
        # launch is reported as pid=-1 with a GLib.Error, without finish().
        self.spawn_callback(self, pid, error, self.spawn_user_data)

    def exit_child(self, wait_status: int) -> None:
        self.handlers["child-exited"](self, wait_status)


class _FakeViewModel:
    def __init__(self, spec: TerminalLaunchSpec | None = None) -> None:
        self.spec = spec or TerminalLaunchSpec(
            argv=("/bin/example", "--flag"),
            env={"ONLY_THIS": "value", "PATH": "/custom/bin"},
            cwd="/workspace/terminal-cwd",
            scrollback_lines=321,
            title="example",
        )
        self.spec_error: Exception | None = None
        self.spec_entered = asyncio.Event()
        self.spec_release = asyncio.Event()
        self.spec_block = False
        self.started_calls: list[int] = []
        self.exited_calls: list[int] = []
        self.calls: list[tuple[str, int]] = []
        self.lifecycle_calls: list[tuple[int, asyncio.Future[int]]] = []
        self.started_error: Exception | None = None
        self.exited_error: Exception | None = None
        self.lifecycle_started = asyncio.Event()

    async def launch_spec(self) -> TerminalLaunchSpec:
        self.spec_entered.set()
        if self.spec_block:
            await self.spec_release.wait()
        if self.spec_error is not None:
            raise self.spec_error
        return self.spec

    async def report_started(self, pid: int) -> None:
        self.calls.append(("started", pid))
        self.started_calls.append(pid)
        if self.started_error is not None:
            raise self.started_error

    async def report_exited(self, wait_status: int) -> None:
        self.calls.append(("exited", wait_status))
        self.exited_calls.append(wait_status)
        if self.exited_error is not None:
            raise self.exited_error

    async def report_lifecycle(
        self,
        pid: int,
        exit_status: asyncio.Future[int],
        *,
        started_report_result: asyncio.Future[Exception | None] | None = None,
    ) -> tuple[Exception | None, Exception | None]:
        self.lifecycle_calls.append((pid, exit_status))
        self.lifecycle_started.set()
        started_error: Exception | None = None
        try:
            await self.report_started(pid)
        except Exception as error:  # noqa: BLE001 -- The fake records lifecycle RPC failures for assertions.
            started_error = error
        if started_report_result is not None and not started_report_result.done():
            started_report_result.set_result(started_error)
        wait_status = await asyncio.shield(exit_status)
        exited_error: Exception | None = None
        try:
            await self.report_exited(wait_status)
        except Exception as error:  # noqa: BLE001 -- The fake records lifecycle RPC failures for assertions.
            exited_error = error
        return started_error, exited_error


def _adapter(monkeypatch: pytest.MonkeyPatch) -> tuple[object, object]:
    module = importlib.import_module("aconsole.ui.views.terminal.session")
    monkeypatch.setattr(module, "spawn_task", asyncio.create_task)
    return module, module.Vte


def _spawn_flags(args: tuple[object, ...]) -> int:
    flags = args[4]
    assert isinstance(flags, int)
    return flags


def test_b5a_terminal_session_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        module, vte = _adapter(monkeypatch)
        terminal = _FakeTerminal()
        vm = _FakeViewModel()
        session = module.TerminalSession(terminal, vm)  # pyright: ignore[reportAttributeAccessIssue] -- Adapter module is imported dynamically for this GUI-free test.
        assert vm.lifecycle_calls == []
        assert terminal.spawn_args is None

        first = asyncio.create_task(session.start())
        second = asyncio.create_task(session.start())
        await _bounded(terminal.spawn_called.wait())
        args = terminal.spawn_args
        assert args is not None
        assert args[1] == vm.spec.cwd
        assert isinstance(args[2], (list, tuple))
        assert tuple(args[2]) == vm.spec.argv
        assert isinstance(args[3], (list, tuple))
        assert tuple(args[3]) == tuple(
            f"{key}={value}" for key, value in vm.spec.env.items()
        )
        assert _spawn_flags(args) & int(vte.SPAWN_NO_PARENT_ENVV)  # pyright: ignore[reportAttributeAccessIssue] -- VTE constants come from the dynamically imported adapter module.
        assert args[5] is None  # no child setup
        assert terminal.audible_bell is False
        assert terminal.scrollback_lines == vm.spec.scrollback_lines
        assert vm.lifecycle_calls == []  # reporting starts after spawn callback

        terminal.complete_spawn(8123)
        assert await _bounded(first) == 8123
        assert await _bounded(second) == 8123
        await _bounded(vm.lifecycle_started.wait())
        await _bounded(session.started_report())
        assert vm.started_calls == [8123]
        assert len(vm.lifecycle_calls) == 1
        assert terminal.spawn_args is args
        terminal.exit_child(0)
        assert await _bounded(session.wait_finished()) == (None, None)

    asyncio.run(exercise())


def test_b5a_terminal_start_is_lazy_and_cancelled_waiter_does_not_cancel_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        module, _ = _adapter(monkeypatch)
        terminal = _FakeTerminal()
        vm = _FakeViewModel()
        vm.spec_block = True
        session = module.TerminalSession(terminal, vm)  # pyright: ignore[reportAttributeAccessIssue] -- Adapter module is imported dynamically for this GUI-free test.
        assert not vm.spec_entered.is_set()

        waiter = asyncio.create_task(session.start())
        await _bounded(vm.spec_entered.wait())
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await _bounded(waiter)
        vm.spec_release.set()
        await _bounded(terminal.spawn_called.wait())
        terminal.complete_spawn(91)
        assert await _bounded(session.start()) == 91
        await _bounded(vm.lifecycle_started.wait())
        await _bounded(session.started_report())
        assert vm.started_calls == [91]
        terminal.exit_child(0)
        assert await _bounded(session.wait_finished()) == (None, None)

    asyncio.run(exercise())


def test_b5a_terminal_reports_raw_exit_status_after_started_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        module, _ = _adapter(monkeypatch)
        terminal = _FakeTerminal()
        vm = _FakeViewModel()
        session = module.TerminalSession(terminal, vm)  # pyright: ignore[reportAttributeAccessIssue] -- Adapter module is imported dynamically for this GUI-free test.
        start = asyncio.create_task(session.start())
        await _bounded(terminal.spawn_called.wait())
        terminal.complete_spawn(55)
        assert await _bounded(start) == 55
        await _bounded(session.started_report())
        terminal.exit_child(0x7F00)
        assert await _bounded(session.wait_finished()) == (None, None)
        assert vm.calls == [("started", 55), ("exited", 0x7F00)]
        assert vm.exited_calls == [0x7F00]

    asyncio.run(exercise())


def test_b5a_terminal_reports_spawn_and_launch_errors_without_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        module, _ = _adapter(monkeypatch)
        launch_error = RuntimeError("launch spec failed")
        terminal = _FakeTerminal()
        vm = _FakeViewModel()
        vm.spec_error = launch_error
        session = module.TerminalSession(terminal, vm)  # pyright: ignore[reportAttributeAccessIssue] -- Adapter module is imported dynamically for this GUI-free test.
        with pytest.raises(RuntimeError, match="launch spec failed"):
            await _bounded(session.start())
        assert terminal.spawn_args is None
        assert vm.lifecycle_calls == []

        terminal2 = _FakeTerminal()
        vm2 = _FakeViewModel()
        session2 = module.TerminalSession(terminal2, vm2)  # pyright: ignore[reportAttributeAccessIssue] -- Adapter module is imported dynamically for this GUI-free test.
        start = asyncio.create_task(session2.start())
        await _bounded(terminal2.spawn_called.wait())
        terminal2.complete_spawn(-1, RuntimeError("spawn failed"))
        with pytest.raises(RuntimeError, match="spawn failed"):
            await _bounded(start)
        assert vm2.lifecycle_calls == []

    asyncio.run(exercise())


def test_b5a_terminal_cancel_before_spawn_cancels_spec_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        module, _ = _adapter(monkeypatch)
        terminal = _FakeTerminal()
        vm = _FakeViewModel()
        vm.spec_block = True
        session = module.TerminalSession(terminal, vm)  # pyright: ignore[reportAttributeAccessIssue] -- Adapter module is imported dynamically for this GUI-free test.
        start = asyncio.create_task(session.start())
        await _bounded(vm.spec_entered.wait())
        assert session.cancel_pending_spawn() is True
        with pytest.raises(asyncio.CancelledError):
            await _bounded(start)
        assert terminal.spawn_args is None
        assert vm.lifecycle_calls == []
        assert session.cancel_pending_spawn() is False

    asyncio.run(exercise())


def test_b5a_terminal_cancel_before_worker_starts_settles_all_waiters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        module, _ = _adapter(monkeypatch)
        terminal = _FakeTerminal()
        vm = _FakeViewModel()
        session = module.TerminalSession(terminal, vm)  # pyright: ignore[reportAttributeAccessIssue] -- Adapter module is imported dynamically for this GUI-free test.
        cancel_results: list[bool] = []

        first = asyncio.create_task(session.start())
        second = asyncio.create_task(session.start())
        asyncio.get_running_loop().call_soon(
            lambda: cancel_results.append(session.cancel_pending_spawn())
        )

        with pytest.raises(asyncio.CancelledError):
            await _bounded(first)
        with pytest.raises(asyncio.CancelledError):
            await _bounded(second)
        with pytest.raises(asyncio.CancelledError):
            await _bounded(session.started_report())
        with pytest.raises(asyncio.CancelledError):
            await _bounded(session.wait_finished())

        assert cancel_results == [True]
        assert not vm.spec_entered.is_set()
        assert session.cancel_pending_spawn() is False

    asyncio.run(exercise())


def test_b5a_terminal_late_success_after_cancel_still_reports_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        module, _ = _adapter(monkeypatch)
        terminal = _FakeTerminal()
        vm = _FakeViewModel()
        session = module.TerminalSession(terminal, vm)  # pyright: ignore[reportAttributeAccessIssue] -- Adapter module is imported dynamically for this GUI-free test.
        start = asyncio.create_task(session.start())
        await _bounded(terminal.spawn_called.wait())
        assert session.cancel_pending_spawn() is True
        assert vm.lifecycle_calls == []
        terminal.complete_spawn(410)
        assert await _bounded(start) == 410
        await _bounded(session.started_report())
        assert vm.started_calls == [410]
        assert len(vm.lifecycle_calls) == 1
        terminal.exit_child(0x123)
        assert await _bounded(session.wait_finished()) == (None, None)
        assert vm.exited_calls == [0x123]

    asyncio.run(exercise())


class _ProbeViewModel:
    def __init__(self, events: list[tuple[str, int]]) -> None:
        self.events = events

    async def launch_spec(self) -> TerminalLaunchSpec:
        return TerminalLaunchSpec(
            argv=("/usr/bin/false",),
            env={},
            cwd="/workspace/terminal-cwd",
            scrollback_lines=0,
            title="vte-probe",
        )

    async def report_started(self, pid: int) -> None:
        self.events.append(("started", pid))

    async def report_exited(self, wait_status: int) -> None:
        self.events.append(("exited", wait_status))

    async def report_lifecycle(
        self,
        pid: int,
        exit_status: asyncio.Future[int],
        *,
        started_report_result: asyncio.Future[Exception | None] | None = None,
    ) -> tuple[Exception | None, Exception | None]:
        started_error: Exception | None = None
        try:
            await self.report_started(pid)
        except Exception as report_error:  # noqa: BLE001 -- preserve the real lifecycle contract.
            started_error = report_error
        if started_report_result is not None and not started_report_result.done():
            started_report_result.set_result(started_error)
        wait_status = await asyncio.shield(exit_status)
        exited_error: Exception | None = None
        try:
            await self.report_exited(wait_status)
        except Exception as report_error:  # noqa: BLE001 -- preserve the real lifecycle contract.
            exited_error = report_error
        return started_error, exited_error


async def _wait_probe_lifecycle(session: object) -> tuple[int, tuple[Exception | None, Exception | None]]:
    pid = await session.start()  # pyright: ignore[reportAttributeAccessIssue] -- The helper receives the concrete TerminalSession instance.
    _ = await session.started_report()  # pyright: ignore[reportAttributeAccessIssue] -- The helper receives the concrete TerminalSession instance.
    errors = await session.wait_finished()  # pyright: ignore[reportAttributeAccessIssue] -- The helper receives the concrete TerminalSession instance.
    return pid, errors


async def _settle_cancelled_probe(session: object, outcome: dict[str, object]) -> None:
    outcome["timed_out"] = True
    _ = session.cancel_pending_spawn()  # pyright: ignore[reportAttributeAccessIssue] -- The helper receives the concrete TerminalSession instance.
    try:
        pid, lifecycle_errors = await asyncio.wait_for(
            _wait_probe_lifecycle(session), timeout=5.0
        )
        outcome["pid"] = pid
        outcome["lifecycle_errors"] = lifecycle_errors
        outcome["cleanup_settled"] = True
    except TimeoutError as cleanup_error:
        outcome["cleanup_error"] = cleanup_error
    except BaseException as settled_error:  # noqa: BLE001 -- a spawn error means the cancelled callback settled without a child.
        outcome["cleanup_settled"] = True
        outcome["cleanup_error"] = settled_error


async def _run_vte_probe(application: object, session: object, outcome: dict[str, object]) -> None:
    try:
        pid, lifecycle_errors = await asyncio.wait_for(
            _wait_probe_lifecycle(session), timeout=5.0
        )
        outcome["pid"] = pid
        outcome["lifecycle_errors"] = lifecycle_errors
    except TimeoutError:
        # Cancel only a pending spawn; the child exits by itself.
        await _settle_cancelled_probe(session, outcome)
    except BaseException as probe_error:  # noqa: BLE001 -- report async failures back to pytest after GTK exits.
        outcome["error"] = probe_error
    finally:
        application.quit()  # pyright: ignore[reportAttributeAccessIssue] -- This is the Gio.Application passed to activate.
        application.release()  # pyright: ignore[reportAttributeAccessIssue] -- This is the Gio.Application passed to activate.


def _test_vte_spawn_with_app(
    gtk: object,
    gio: object,
    vte: object,
    run_app: Callable[..., int],
    spawn_task: Callable[..., asyncio.Task[None]],
) -> tuple[list[tuple[str, int]], list[int], dict[str, object]]:
    gtk.init()  # pyright: ignore[reportAttributeAccessIssue] -- Gtk is loaded after the required GI version is selected.
    events: list[tuple[str, int]] = []
    observed_exit: list[int] = []
    outcome: dict[str, object] = {}
    probe_tasks: list[asyncio.Task[None]] = []
    probe_loops: list[asyncio.AbstractEventLoop] = []
    app = gio.Application(  # pyright: ignore[reportAttributeAccessIssue] -- Gio is loaded after the required GI version is selected.
        application_id="org.aconsole.VteSpawnProbe",
        flags=gio.ApplicationFlags.NON_UNIQUE,  # pyright: ignore[reportAttributeAccessIssue] -- The probe requests a private test application.
    )

    def activate(application: object) -> None:
        terminal = vte.Terminal()  # pyright: ignore[reportAttributeAccessIssue] -- VTE is loaded after the required GI version is selected.
        _ = terminal.connect("child-exited", lambda _terminal, status: observed_exit.append(status))
        from aconsole.ui.views.terminal.session import TerminalSession

        session = TerminalSession(terminal, _ProbeViewModel(events))  # pyright: ignore[reportArgumentType] -- The probe implements TerminalViewModel's async surface.
        probe_loops.append(asyncio.get_running_loop())
        application.hold()  # pyright: ignore[reportAttributeAccessIssue] -- Hold the app open until the async probe settles.
        probe_tasks.append(spawn_task(_run_vte_probe(application, session, outcome)))

    def create_app() -> object:
        _ = app.connect("activate", activate)
        return app

    status = run_app(create_app, ["test_b5a_terminal_real_vte_spawn_async"])
    assert len(probe_loops) == 1
    loop = probe_loops[0]
    try:
        pending = [task for task in probe_tasks if not task.done()]
        if pending:
            loop.run_until_complete(
                asyncio.wait_for(asyncio.gather(*pending), timeout=5.0)
            )
        assert all(task.done() for task in probe_tasks)
        assert status == 0
        return events, observed_exit, outcome
    finally:
        if not loop.is_running() and not loop.is_closed():
            loop.close()
        asyncio.set_event_loop(None)


def test_b5a_real_vte_spawn_async_reports_exit_and_raw_wait_status() -> None:
    """Exercise the actual VTE spawn callback without presenting a window."""
    import os

    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Vte", "3.91")
    from gi.repository import Gio, Gtk, Vte

    from aconsole.ui.bootstrap import run_app, spawn_task

    events, observed_exit, outcome = _test_vte_spawn_with_app(
        Gtk, Gio, Vte, run_app, spawn_task
    )
    assert "error" not in outcome, repr(outcome.get("error"))
    assert "cleanup_error" not in outcome, repr(outcome.get("cleanup_error"))
    assert outcome.get("timed_out") is not True
    assert outcome.get("lifecycle_errors") == (None, None)
    assert len(observed_exit) == 1
    raw_status = observed_exit[0]
    assert os.WIFEXITED(raw_status)
    assert os.WEXITSTATUS(raw_status) == 1
    assert events == [("started", outcome["pid"]), ("exited", raw_status)]
