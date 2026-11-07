"""Delayed responses must not replace newer UI state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Coroutine

from aconsole.contracts.common import TabId
from aconsole.contracts.events import TabRemoved
from aconsole.contracts.ipc import EventEnvelope
from aconsole.ui.client.store import CoreStore


def test_event_subscriber_failure_keeps_sequence_and_other_subscribers() -> None:
    store = CoreStore()
    seen = []

    def broken(_event: object, _store: CoreStore) -> None:
        message = "subscriber failure"
        raise RuntimeError(message)

    store.subscribe(broken)
    store.subscribe(lambda event, _store: seen.append(event))
    event = TabRemoved(tab_id=TabId("tab-a"))
    assert store.apply(EventEnvelope(seq=1, at=datetime.now(UTC), event=event))
    assert store.seq == 1
    assert seen == [event]
    assert not store.reconnecting


def test_late_graph_page_does_not_append_to_new_scope() -> None:
    from aconsole.ui.views.review.git_pane import GitPane

    async def run() -> None:
        loop = asyncio.get_running_loop()
        answer = loop.create_future()
        entered = asyncio.Event()
        rows = []

        async def graph(_scope: object, _cursor: object) -> SimpleNamespace:
            entered.set()
            return await answer

        pane = SimpleNamespace(
            _cursor="old", _loading=False, _generation=1, _version="v1", scope=None,
            viewmodel=SimpleNamespace(graph=graph), _builder=SimpleNamespace(commit=lambda row: row),
            store=SimpleNamespace(append=rows.append), toast=lambda _text: None,
        )
        task = asyncio.create_task(GitPane.load_more(pane))
        await entered.wait()
        pane.scope = TabId("tab-b")
        pane._generation += 1
        pane._cursor = "new"
        answer.set_result(SimpleNamespace(next_cursor="wrong", nodes=("old-node",), version="v1"))
        await task
        assert pane._cursor == "new"
        assert rows == []
        assert not pane._loading

    asyncio.run(run())


def test_older_checkpoints_cannot_replace_newer_result() -> None:
    from aconsole.ui.views.review.page import ReviewPage

    async def run() -> None:
        answers = [asyncio.get_running_loop().create_future() for _ in range(2)]
        entered = asyncio.Queue()
        calls = 0
        shown = []

        async def checkpoints() -> tuple[str, ...]:
            nonlocal calls
            index = calls
            calls += 1
            entered.put_nowait(index)
            return await answers[index]

        page = SimpleNamespace(
            _checkpoint_generation=0, viewmodel=SimpleNamespace(checkpoints=checkpoints),
            checkpoint_list=SimpleNamespace(remove_all=shown.clear, append=shown.append),
            _checkpoint_row=lambda point: point, _show_checkpoints_message=shown.append,
        )
        old = asyncio.create_task(ReviewPage.load_checkpoints(page))
        await entered.get()
        new = asyncio.create_task(ReviewPage.load_checkpoints(page))
        await entered.get()
        answers[1].set_result(("new",))
        await new
        answers[0].set_result(("old",))
        await old
        assert shown == ["new"]

    asyncio.run(run())


def test_git_operations_do_not_overlap() -> None:
    from aconsole.ui.views.review.git_actions import GitActions

    async def run() -> None:
        active = 0
        completed = []

        async def perform(work: Coroutine[object, object, None]) -> None:
            nonlocal active
            active += 1
            assert active == 1
            await asyncio.sleep(0)
            await work
            active -= 1

        async def work() -> None:
            completed.append("operation")

        actions = SimpleNamespace(_operation_lock=asyncio.Lock(), _perform=perform)
        await asyncio.gather(GitActions._guarded(actions, work()), GitActions._guarded(actions, work()))
        assert completed == ["operation", "operation"]

    asyncio.run(run())


def test_snapshot_and_connection_subscribers_fail_independently() -> None:
    from aconsole.core.infrastructure.fake.scenarios import demo_snapshot

    store = CoreStore()
    seen = []

    def broken(_store: CoreStore) -> None:
        message = "subscriber failure"
        raise RuntimeError(message)

    store.subscribe_snapshot(broken)
    store.subscribe_snapshot(lambda _store: seen.append("snapshot"))
    store.subscribe_connection(broken)
    store.subscribe_connection(lambda _store: seen.append("connection"))
    store.apply_snapshot(demo_snapshot(), 1)
    assert seen == ["snapshot", "connection"]
    store.set_connecting()
    assert seen[-1] == "connection"


def test_clearing_git_inspector_discards_pending_detail() -> None:
    from aconsole.ui.views.review.git_pane import GitPane

    async def run() -> None:
        answer = asyncio.get_running_loop().create_future()
        entered = asyncio.Event()
        shown = []

        async def file(_source: object, _path: str) -> str:
            entered.set()
            return await answer

        widget = SimpleNamespace(set_visible=lambda _value: None, set_text=lambda _text: None)
        pane = SimpleNamespace(
            _file_generation=0, _list_generation=0, source="source", selected_path="f",
            inspect_meta=widget, mode_choice=widget, inspect_title=widget,
            file_list=SimpleNamespace(remove_all=lambda: None), _clear_diff=lambda: None,
            viewmodel=SimpleNamespace(file=file), _show_diff=shown.append,
        )
        task = asyncio.create_task(GitPane._load_file(pane, "source", "f"))
        await entered.wait()
        GitPane._clear_inspector(pane)
        answer.set_result("old detail")
        await task
        assert shown == []

    asyncio.run(run())
