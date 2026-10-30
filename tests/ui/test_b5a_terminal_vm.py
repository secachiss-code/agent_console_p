"""The terminal view-model forwards lifecycle calls using contract types."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import override

import msgspec
import pytest

from aconsole.contracts.app import (
    EmptyResult,
    ExitedParams,
    StartedParams,
    TabIdParams,
    TerminalLaunchSpec,
)
from aconsole.contracts.common import TabId
from aconsole.ui.client.core_client import CoreClient


@dataclass(frozen=True)
class _Call:
    method: str
    params: msgspec.Struct
    result_type: type[msgspec.Struct]


class _RecordingClient(CoreClient):
    def __init__(self, answer: msgspec.Struct) -> None:
        super().__init__("unused.sock")
        self.answer = answer
        self.calls: list[_Call] = []
        self.error: Exception | None = None

    @override
    async def request[T: msgspec.Struct](
        self,
        method: str,
        params: msgspec.Struct,
        result_type: type[T],
        *,
        long: bool = False,
    ) -> T:
        _ = long
        self.calls.append(_Call(method, params, result_type))
        if self.error is not None:
            raise self.error
        return self.answer  # pyright: ignore[reportReturnType] -- the recording stub returns the configured contract result


def test_b5a_terminal_vm_requests_launch_spec_with_contract_types() -> None:
    from aconsole.ui.viewmodels.terminal_vm import TerminalViewModel

    tab_id = TabId("01J9Z5W6FX1M2A3B4C5D6E7F8G")
    spec = TerminalLaunchSpec(
        argv=("/bin/sh", "-l"),
        env={"TERM": "xterm-256color"},
        cwd="/workspace/project",
        scrollback_lines=5000,
        title="AConsole",
    )
    client = _RecordingClient(spec)
    view_model = TerminalViewModel(client, tab_id)

    result = asyncio.run(view_model.launch_spec())

    assert result is spec
    assert client.calls == [
        _Call("tab/launchSpec", TabIdParams(tab_id=tab_id), TerminalLaunchSpec)
    ]


def test_b5a_terminal_vm_reports_started_with_contract_types() -> None:
    from aconsole.ui.viewmodels.terminal_vm import TerminalViewModel

    tab_id = TabId("01J9Z5W6FX1M2A3B4C5D6E7F8G")
    client = _RecordingClient(EmptyResult())
    view_model = TerminalViewModel(client, tab_id)

    asyncio.run(view_model.report_started(2345))

    assert client.calls == [
        _Call("tab/started", StartedParams(tab_id=tab_id, pid=2345), EmptyResult)
    ]


def test_b5a_terminal_vm_reports_exited_with_raw_wait_status() -> None:
    from aconsole.ui.viewmodels.terminal_vm import TerminalViewModel

    tab_id = TabId("01J9Z5W6FX1M2A3B4C5D6E7F8G")
    client = _RecordingClient(EmptyResult())
    view_model = TerminalViewModel(client, tab_id)
    wait_status = 7 << 8

    asyncio.run(view_model.report_exited(wait_status))

    assert client.calls == [
        _Call(
            "tab/exited",
            ExitedParams(tab_id=tab_id, wait_status=wait_status),
            EmptyResult,
        )
    ]


@pytest.mark.parametrize("operation", ["launch", "started", "exited"])
def test_b5a_terminal_vm_propagates_client_errors_unchanged(operation: str) -> None:
    from aconsole.ui.viewmodels.terminal_vm import TerminalViewModel

    async def exercise(view_model: TerminalViewModel) -> object:
        if operation == "launch":
            return await view_model.launch_spec()
        if operation == "started":
            await view_model.report_started(2345)
            return None
        await view_model.report_exited(7 << 8)
        return None

    error = RuntimeError("core unavailable")
    client = _RecordingClient(EmptyResult())
    client.error = error
    view_model = TerminalViewModel(client, TabId("01J9Z5W6FX1M2A3B4C5D6E7F8G"))

    with pytest.raises(RuntimeError) as raised:
        asyncio.run(exercise(view_model))

    assert raised.value is error
