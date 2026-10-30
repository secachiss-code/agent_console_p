"""Async palette view-model tests using a controllable CoreClient double."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import override

import msgspec
import pytest

from aconsole.contracts.app import (
    CommandResult,
    PaletteArgument,
    PaletteExecuteParams,
    PaletteItem,
    PaletteItemsResult,
    PaletteKind,
    PaletteQuery,
    ProjectId,
)
from aconsole.contracts.common import TabId
from aconsole.ui.client.core_client import CoreClient


@dataclass
class _LoadPlan:
    result: PaletteItemsResult
    started: asyncio.Event | None = None
    release: asyncio.Event | None = None


@dataclass(frozen=True)
class _Request:
    method: str
    params: msgspec.Struct
    result_type: type[msgspec.Struct]


class _PaletteClient(CoreClient):
    def __init__(self, loads: list[_LoadPlan]) -> None:
        super().__init__("unused.sock")
        self.loads = loads
        self.requests: list[_Request] = []
        self.execute_started: asyncio.Event | None = None
        self.execute_release: asyncio.Event | None = None
        self.execute_error: Exception | None = None
        self.execute_result = CommandResult(ok=True, toast="Done")

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
        self.requests.append(_Request(method, params, result_type))
        if method == "palette/items":
            plan = self.loads.pop(0)
            if plan.started is not None:
                plan.started.set()
            if plan.release is not None:
                await plan.release.wait()
            return plan.result  # pyright: ignore[reportReturnType] -- the fake returns the configured contract result
        if method == "palette/execute":
            if self.execute_started is not None:
                self.execute_started.set()
            if self.execute_release is not None:
                await self.execute_release.wait()
            if self.execute_error is not None:
                raise self.execute_error
            return self.execute_result  # pyright: ignore[reportReturnType] -- the fake returns the configured contract result
        message = f"unexpected palette request: {method}"
        raise AssertionError(message)


def _item(
    item_id: str,
    title: str,
    *,
    aliases: tuple[str, ...] = (),
    enabled: bool = True,
    argument: PaletteArgument | None = None,
) -> PaletteItem:
    return PaletteItem(
        id=item_id,
        kind=PaletteKind.COMMAND,
        title=title,
        subtitle=None,
        keywords=(),
        aliases=aliases,
        score_hint=1.0,
        enabled=enabled,
        disabled_reason=None if enabled else "Unavailable",
        argument=argument,
        shortcut=None,
    )


def _context() -> PaletteQuery:
    return PaletteQuery(
        tab_id=TabId("01J9Z5W6FX1M2A3B4C5D6E7F8G"),
        project_id=ProjectId("project-one"),
    )


def _items_result(*items: PaletteItem) -> PaletteItemsResult:
    return PaletteItemsResult(items=items)


def test_b7_palette_loads_once_and_searches_cached_items_locally() -> None:
    from aconsole.ui.viewmodels.palette_vm import PaletteViewModel

    async def exercise() -> None:
        switch = _item("switch", "Switch agent", aliases=("swi",))
        settings = _item("settings", "Open settings", aliases=("set",))
        client = _PaletteClient([_LoadPlan(_items_result(switch, settings))])
        view_model = PaletteViewModel(client)

        await view_model.open(_context())
        assert view_model.opened
        assert not view_model.loading
        assert view_model.items == (settings, switch)
        assert client.requests == [
            _Request("palette/items", _context(), PaletteItemsResult)
        ]

        view_model.search("swi")

        assert view_model.query == "swi"
        assert view_model.items == (switch,)
        assert len(client.requests) == 1

    asyncio.run(exercise())


def test_b7_palette_execute_sends_argument_and_opening_context() -> None:
    from aconsole.ui.viewmodels.palette_vm import PaletteViewModel

    async def exercise() -> None:
        argument = PaletteArgument(
            label="Agent",
            choices=(("claude", "Claude"), ("codex", "Codex")),
        )
        codex = _item("switch-agent", "Switch agent", argument=argument)
        client = _PaletteClient([_LoadPlan(_items_result(codex))])
        view_model = PaletteViewModel(client)
        context = _context()
        await view_model.open(context)
        view_model.select(codex.id)

        selected_argument = "codex"
        result = await view_model.execute(selected_argument)

        assert result is client.execute_result
        assert client.requests[-1] == _Request(
            "palette/execute",
            PaletteExecuteParams(
                item_id=codex.id,
                tab_id=context.tab_id,
                project_id=context.project_id,
                argument=selected_argument,
            ),
            CommandResult,
        )

    asyncio.run(exercise())


def test_b7_palette_rejects_disabled_invalid_selection_and_arguments() -> None:
    from aconsole.ui.viewmodels.palette_vm import PaletteViewModel

    async def exercise() -> None:
        choices = PaletteArgument(label="Agent", choices=(("codex", "Codex"),))
        disabled = _item("disabled", "Disabled command", enabled=False)
        needs_argument = _item("agent", "Select agent", argument=choices)
        client = _PaletteClient([_LoadPlan(_items_result(disabled, needs_argument))])
        view_model = PaletteViewModel(client)
        await view_model.open(_context())

        with pytest.raises(ValueError, match="palette item is unavailable"):
            view_model.select(disabled.id)
        with pytest.raises(ValueError, match="palette item is unavailable"):
            view_model.select("missing")
        assert view_model.selected is None

        view_model.select(needs_argument.id)
        with pytest.raises(ValueError, match="invalid palette argument"):
            await view_model.execute()
        invalid_choice = "not-a-choice"
        with pytest.raises(ValueError, match="invalid palette argument"):
            await view_model.execute(invalid_choice)
        assert [request.method for request in client.requests] == ["palette/items"]

    asyncio.run(exercise())


def test_b7_palette_rejects_argument_for_item_without_argument_spec() -> None:
    from aconsole.ui.viewmodels.palette_vm import PaletteViewModel

    async def exercise() -> None:
        item = _item("run", "Run command")
        client = _PaletteClient([_LoadPlan(_items_result(item))])
        view_model = PaletteViewModel(client)
        await view_model.open(_context())
        view_model.select(item.id)

        argument = "unexpected"
        with pytest.raises(ValueError, match="invalid palette argument"):
            await view_model.execute(argument)

        assert [request.method for request in client.requests] == ["palette/items"]

    asyncio.run(exercise())


def test_b7_palette_close_discards_pending_load_and_new_open_wins() -> None:
    from aconsole.ui.viewmodels.palette_vm import PaletteViewModel

    async def exercise() -> None:
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        stale_item = _item("stale", "Stale result")
        fresh_item = _item("fresh", "Fresh result")
        client = _PaletteClient(
            [
                _LoadPlan(_items_result(stale_item), first_started, release_first),
                _LoadPlan(_items_result(fresh_item)),
            ]
        )
        view_model = PaletteViewModel(client)
        first_open = asyncio.create_task(view_model.open(_context()))
        await asyncio.wait_for(first_started.wait(), timeout=1)

        view_model.close()
        assert not view_model.opened
        await view_model.open(_context())
        release_first.set()
        await first_open

        assert view_model.opened
        assert view_model.items == (fresh_item,)
        assert [request.method for request in client.requests] == [
            "palette/items",
            "palette/items",
        ]

    asyncio.run(exercise())


def test_b7_palette_uses_latest_query_when_load_finishes() -> None:
    from aconsole.ui.viewmodels.palette_vm import PaletteViewModel

    async def exercise() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        claude = _item("claude", "Claude", aliases=("claude",))
        codex = _item("codex", "Codex", aliases=("codex",))
        client = _PaletteClient(
            [_LoadPlan(_items_result(claude, codex), started, release)]
        )
        view_model = PaletteViewModel(client)
        loading = asyncio.create_task(view_model.open(_context()))
        await asyncio.wait_for(started.wait(), timeout=1)

        view_model.search("codex")
        release.set()
        await loading

        assert view_model.query == "codex"
        assert view_model.items == (codex,)

    asyncio.run(exercise())


def test_b7_palette_changed_search_clears_selection_before_execute() -> None:
    from aconsole.ui.viewmodels.palette_vm import PaletteViewModel

    async def exercise() -> None:
        alpha = _item("alpha", "Alpha command", aliases=("alpha",))
        beta = _item("beta", "Beta command", aliases=("beta",))
        client = _PaletteClient([_LoadPlan(_items_result(alpha, beta))])
        view_model = PaletteViewModel(client)
        await view_model.open(_context())
        view_model.search("alpha")
        view_model.select(alpha.id)

        view_model.search("beta")

        assert view_model.items == (beta,)
        assert view_model.selected is None
        with pytest.raises(ValueError, match="no palette item selected"):
            await view_model.execute()
        assert [request.method for request in client.requests] == ["palette/items"]

    asyncio.run(exercise())


def test_b7_palette_repeating_query_preserves_selection() -> None:
    from aconsole.ui.viewmodels.palette_vm import PaletteViewModel

    async def exercise() -> None:
        item = _item("switch", "Switch agent", aliases=("swi",))
        client = _PaletteClient([_LoadPlan(_items_result(item))])
        view_model = PaletteViewModel(client)
        await view_model.open(_context())
        view_model.search("swi")
        view_model.select(item.id)

        view_model.search("swi")

        assert view_model.selected is item

    asyncio.run(exercise())


def test_b7_palette_back_keeps_query_and_results() -> None:
    from aconsole.ui.viewmodels.palette_vm import PaletteViewModel

    async def exercise() -> None:
        item = _item("codex", "Codex", aliases=("codex",))
        other = _item("claude", "Claude", aliases=("claude",))
        client = _PaletteClient([_LoadPlan(_items_result(item, other))])
        view_model = PaletteViewModel(client)
        await view_model.open(_context())
        view_model.search("codex")
        view_model.select(item.id)

        view_model.back()

        assert view_model.selected is None
        assert view_model.query == "codex"
        assert view_model.items == (item,)

    asyncio.run(exercise())


def test_b7_palette_ignores_second_execute_while_first_is_pending() -> None:
    from aconsole.ui.viewmodels.palette_vm import PaletteViewModel

    async def exercise() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        item = _item("run", "Run command")
        client = _PaletteClient([_LoadPlan(_items_result(item))])
        client.execute_started = started
        client.execute_release = release
        view_model = PaletteViewModel(client)
        await view_model.open(_context())
        view_model.select(item.id)
        first_execute = asyncio.create_task(view_model.execute())
        await asyncio.wait_for(started.wait(), timeout=1)

        second_result = await view_model.execute()
        release.set()
        first_result = await first_execute

        assert second_result is None
        assert first_result is client.execute_result
        assert sum(request.method == "palette/execute" for request in client.requests) == 1

    asyncio.run(exercise())


def test_b7_palette_late_execute_result_does_not_change_new_opening() -> None:
    from aconsole.ui.viewmodels.palette_vm import PaletteViewModel

    async def exercise() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        old_item = _item("old", "Old command")
        new_item = _item("new", "New command")
        client = _PaletteClient(
            [_LoadPlan(_items_result(old_item)), _LoadPlan(_items_result(new_item))]
        )
        client.execute_started = started
        client.execute_release = release
        view_model = PaletteViewModel(client)
        await view_model.open(_context())
        view_model.select(old_item.id)
        execution = asyncio.create_task(view_model.execute())
        await asyncio.wait_for(started.wait(), timeout=1)

        view_model.close()
        await view_model.open(_context())
        release.set()
        result = await execution

        assert result is client.execute_result
        assert view_model.opened
        assert view_model.items == (new_item,)
        assert view_model.selected is None

    asyncio.run(exercise())


def test_b7_palette_pending_execute_blocks_new_opening_without_mutating_it() -> None:
    from aconsole.ui.viewmodels.palette_vm import PaletteViewModel

    async def exercise() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        old_item = _item("old", "Old command")
        new_item = _item("new", "New command")
        client = _PaletteClient(
            [_LoadPlan(_items_result(old_item)), _LoadPlan(_items_result(new_item))]
        )
        client.execute_started = started
        client.execute_release = release
        view_model = PaletteViewModel(client)
        await view_model.open(_context())
        view_model.select(old_item.id)
        execution = asyncio.create_task(view_model.execute())
        await asyncio.wait_for(started.wait(), timeout=1)

        view_model.close()
        await view_model.open(_context())
        view_model.select(new_item.id)
        new_opening_error = RuntimeError("new opening error")
        view_model.error = new_opening_error

        blocked_result = await view_model.execute()
        release.set()
        completed_result = await execution

        assert blocked_result is None
        assert completed_result is client.execute_result
        assert sum(request.method == "palette/execute" for request in client.requests) == 1
        assert view_model.selected is new_item
        assert view_model.error is new_opening_error

    asyncio.run(exercise())


def test_b7_palette_execute_error_propagates_and_keeps_selection() -> None:
    from aconsole.ui.viewmodels.palette_vm import PaletteViewModel

    async def exercise() -> None:
        item = _item("run", "Run command")
        error = RuntimeError("execute failed")
        client = _PaletteClient([_LoadPlan(_items_result(item))])
        client.execute_error = error
        view_model = PaletteViewModel(client)
        await view_model.open(_context())
        view_model.select(item.id)

        with pytest.raises(RuntimeError) as raised:
            await view_model.execute()

        assert raised.value is error
        assert view_model.selected is item
        assert view_model.error is error

    asyncio.run(exercise())
