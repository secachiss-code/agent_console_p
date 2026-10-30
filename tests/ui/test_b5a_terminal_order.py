"""Ordered terminal lifecycle reporting without GTK dependencies."""

from __future__ import annotations

import asyncio
from typing import Literal, override

import msgspec
import pytest

from aconsole.contracts.app import EmptyResult
from aconsole.contracts.common import TabId
from aconsole.ui.client.core_client import CoreClient


class _LifecycleClient(CoreClient):
    def __init__(self) -> None:
        super().__init__("unused.sock")
        self.started_entered = asyncio.Event()
        self.finish_started = asyncio.Event()
        self.started_returned = asyncio.Event()
        self.started_finished = asyncio.Event()
        self.exited_entered = asyncio.Event()
        self.started_error: Exception | None = None
        self.exited_error: Exception | None = None
        self.calls: list[str] = []

    @override
    async def request[T: msgspec.Struct](
        self,
        method: str,
        params: msgspec.Struct,
        result_type: type[T],
        *,
        long: bool = False,
    ) -> T:
        _ = (params, result_type, long)
        self.calls.append(method)
        if method == "tab/started":
            self.started_entered.set()
            try:
                await self.finish_started.wait()
                if self.started_error is not None:
                    raise self.started_error
                self.started_returned.set()
            finally:
                self.started_finished.set()
        elif method == "tab/exited":
            self.exited_entered.set()
            if self.exited_error is not None:
                raise self.exited_error
        return EmptyResult()  # pyright: ignore[reportReturnType] -- the fake answers lifecycle requests with their contract result


@pytest.mark.parametrize("status_ready", ["before", "during"])
def test_b5a_terminal_order_waits_for_started_and_preserves_ready_status(
    status_ready: Literal["before", "during"],
) -> None:
    from aconsole.ui.viewmodels.terminal_vm import TerminalViewModel

    async def exercise() -> None:
        client = _LifecycleClient()
        view_model = TerminalViewModel(
            client, TabId("01J9Z5W6FX1M2A3B4C5D6E7F8G")
        )
        exit_status: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        raw_wait_status = 7 << 8
        if status_ready == "before":
            exit_status.set_result(raw_wait_status)

        lifecycle = asyncio.create_task(
            view_model.report_lifecycle(2345, exit_status)
        )
        await asyncio.wait_for(client.started_entered.wait(), timeout=1)
        assert client.calls == ["tab/started"]
        assert not client.exited_entered.is_set()

        if status_ready == "during":
            exit_status.set_result(raw_wait_status)
        assert not client.exited_entered.is_set()

        client.finish_started.set()
        await asyncio.wait_for(client.exited_entered.wait(), timeout=1)
        result = await lifecycle

        assert result == (None, None)
        assert client.calls == ["tab/started", "tab/exited"]

    asyncio.run(exercise())


def test_b5a_terminal_order_keeps_started_error_and_still_reports_exit() -> None:
    from aconsole.ui.viewmodels.terminal_vm import TerminalViewModel

    async def exercise() -> None:
        client = _LifecycleClient()
        started_error = RuntimeError("started failed")
        client.started_error = started_error
        view_model = TerminalViewModel(
            client, TabId("01J9Z5W6FX1M2A3B4C5D6E7F8G")
        )
        exit_status: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        exit_status.set_result(7 << 8)

        result_task = asyncio.create_task(
            view_model.report_lifecycle(2345, exit_status)
        )
        await asyncio.wait_for(client.started_entered.wait(), timeout=1)
        assert not client.exited_entered.is_set()
        client.finish_started.set()
        await asyncio.wait_for(client.exited_entered.wait(), timeout=1)

        started_result, exited_result = await result_task
        assert started_result is started_error
        assert exited_result is None
        assert client.calls == ["tab/started", "tab/exited"]

    asyncio.run(exercise())


@pytest.mark.parametrize("started_error", [None, RuntimeError("started failed")])
def test_b5a_terminal_order_publishes_started_result_before_exit(
    started_error: Exception | None,
) -> None:
    from aconsole.ui.viewmodels.terminal_vm import TerminalViewModel

    async def exercise() -> None:
        client = _LifecycleClient()
        client.started_error = started_error
        view_model = TerminalViewModel(
            client, TabId("01J9Z5W6FX1M2A3B4C5D6E7F8G")
        )
        exit_status: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        started_result: asyncio.Future[Exception | None] = (
            asyncio.get_running_loop().create_future()
        )
        lifecycle = asyncio.create_task(
            view_model.report_lifecycle(
                2345, exit_status, started_report_result=started_result
            )
        )
        await asyncio.wait_for(client.started_entered.wait(), timeout=1)
        assert not started_result.done()

        client.finish_started.set()
        await asyncio.wait_for(client.started_finished.wait(), timeout=1)

        assert started_result.done()
        assert started_result.result() is started_error
        assert not client.exited_entered.is_set()

        exit_status.set_result(7 << 8)
        started_outcome, exited_outcome = await lifecycle
        assert started_outcome is started_error
        assert exited_outcome is None

    asyncio.run(exercise())


def test_b5a_terminal_order_returns_exited_error_separately() -> None:
    from aconsole.ui.viewmodels.terminal_vm import TerminalViewModel

    async def exercise() -> None:
        client = _LifecycleClient()
        exited_error = RuntimeError("exited failed")
        client.exited_error = exited_error
        view_model = TerminalViewModel(
            client, TabId("01J9Z5W6FX1M2A3B4C5D6E7F8G")
        )
        client.finish_started.set()
        exit_status: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        exit_status.set_result(7 << 8)

        started_result, exited_result = await view_model.report_lifecycle(
            2345, exit_status
        )

        assert started_result is None
        assert exited_result is exited_error
        assert client.calls == ["tab/started", "tab/exited"]

    asyncio.run(exercise())


def test_b5a_terminal_order_cancellation_during_started_preserves_future() -> None:
    from aconsole.ui.viewmodels.terminal_vm import TerminalViewModel

    async def exercise() -> None:
        client = _LifecycleClient()
        view_model = TerminalViewModel(
            client, TabId("01J9Z5W6FX1M2A3B4C5D6E7F8G")
        )
        exit_status: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        started_result: asyncio.Future[Exception | None] = (
            asyncio.get_running_loop().create_future()
        )
        lifecycle = asyncio.create_task(
            view_model.report_lifecycle(
                2345, exit_status, started_report_result=started_result
            )
        )
        await asyncio.wait_for(client.started_entered.wait(), timeout=1)

        lifecycle.cancel()
        with pytest.raises(asyncio.CancelledError):
            await lifecycle

        assert started_result.cancelled()
        assert not exit_status.cancelled()
        exit_status.set_result(7 << 8)
        assert exit_status.result() == 7 << 8
        assert client.calls == ["tab/started"]
        assert not client.exited_entered.is_set()

    asyncio.run(exercise())


def test_b5a_terminal_order_ignores_cancelled_started_result_future() -> None:
    from aconsole.ui.viewmodels.terminal_vm import TerminalViewModel

    async def exercise() -> None:
        client = _LifecycleClient()
        client.finish_started.set()
        view_model = TerminalViewModel(
            client, TabId("01J9Z5W6FX1M2A3B4C5D6E7F8G")
        )
        exit_status: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        exit_status.set_result(7 << 8)
        started_result: asyncio.Future[Exception | None] = (
            asyncio.get_running_loop().create_future()
        )
        started_result.cancel()

        result = await view_model.report_lifecycle(
            2345, exit_status, started_report_result=started_result
        )

        assert result == (None, None)
        assert started_result.cancelled()
        assert client.calls == ["tab/started", "tab/exited"]

    asyncio.run(exercise())


def test_b5a_terminal_order_cancellation_while_waiting_for_exit_preserves_future() -> None:
    from aconsole.ui.viewmodels.terminal_vm import TerminalViewModel

    async def exercise() -> None:
        client = _LifecycleClient()
        client.finish_started.set()
        view_model = TerminalViewModel(
            client, TabId("01J9Z5W6FX1M2A3B4C5D6E7F8G")
        )
        exit_status: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        lifecycle = asyncio.create_task(
            view_model.report_lifecycle(2345, exit_status)
        )
        await asyncio.wait_for(client.started_returned.wait(), timeout=1)

        lifecycle.cancel()
        with pytest.raises(asyncio.CancelledError):
            await lifecycle

        assert not exit_status.cancelled()
        exit_status.set_result(7 << 8)
        assert exit_status.result() == 7 << 8
        assert client.calls == ["tab/started"]
        assert not client.exited_entered.is_set()

    asyncio.run(exercise())
