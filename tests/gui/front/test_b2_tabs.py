"""B2 sidebar behavior through its documented widgets and drop handler."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, override

import gi
import msgspec
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from aconsole.contracts.app import EmptyResult, RenameParams, ReorderParams  # noqa: E402
from aconsole.contracts.events import TabUpdated  # noqa: E402
from aconsole.contracts.ipc import EventEnvelope  # noqa: E402
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot, empty_snapshot  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabsViewModel  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Awaitable

    from aconsole.contracts.app import AppSnapshot, TabView

_WAIT_SECONDS = 2.0
_SCENARIO_TIMEOUT_SECONDS = 5.0


class _TabsTestApp(Adw.Application):
    """Run each widget scenario on the supported GTK asyncio loop."""

    def __init__(self, scenario: Callable[[_TabsTestApp], Awaitable[None]]) -> None:
        super().__init__(
            application_id="io.github.aconsole.B2TabsTest",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.scenario = scenario
        self.window: Adw.ApplicationWindow | None = None
        self.error: Exception | None = None

    @override
    def do_activate(self) -> None:
        self.hold()
        _ = spawn_task(self._run_scenario())

    async def _run_scenario(self) -> None:
        try:
            self.window = Adw.ApplicationWindow(application=self)
            await asyncio.wait_for(self.scenario(self), timeout=_SCENARIO_TIMEOUT_SECONDS)
        except Exception as error:  # noqa: BLE001 -- surface asynchronous GTK scenario failures in the test.
            self.error = error
        finally:
            if self.window is not None:
                self.window.close()
            self.release()
            self.quit()


def _run_ui(scenario: Callable[[_TabsTestApp], Awaitable[None]]) -> None:
    app = _TabsTestApp(scenario)
    assert run_app(lambda: app, ["aconsole-b2-tabs-test"]) == 0
    assert app.error is None, repr(app.error)


def _model(snapshot: AppSnapshot) -> tuple[CoreClient, TabsViewModel]:
    client = CoreClient("unused")
    client.store.apply_snapshot(snapshot, 0)
    return client, TabsViewModel(client)


async def _until(predicate: Callable[[], bool]) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout=_WAIT_SECONDS)


def test_b2_tab_updated_changes_one_row() -> None:
    snapshot = demo_snapshot()

    async def scenario(app: _TabsTestApp) -> None:
        from aconsole.ui.views.tabs.row import TabRow

        client, tabs = _model(snapshot)
        target = sorted(snapshot.tabs, key=lambda tab: tab.order)[2]
        items_before = [tabs.model.get_item(index) for index in range(tabs.model.get_n_items())]
        item_before = next(item for item in items_before if item is not None and item.tab_id == str(target.id))
        row = TabRow(tabs)
        row.bind_item(item_before)
        assert app.window is not None
        app.window.set_content(row)
        app.window.present()
        model_changes: list[tuple[int, int, int]] = []
        tabs.model.connect(
            "items-changed",
            lambda _model, position, removed, added: model_changes.append((position, removed, added)),
        )
        try:
            updated = msgspec.structs.replace(target, title="Updated visible tab")
            assert client.store.apply(
                EventEnvelope(
                    seq=1,
                    at=updated.status_since,
                    event=TabUpdated(tab=updated, changed=frozenset({"title"})),
                )
            )

            items_after = [tabs.model.get_item(index) for index in range(tabs.model.get_n_items())]
            assert items_after == items_before
            assert model_changes == []
            assert row.tab_id == str(target.id)
            assert row.title_label.get_text() == "Updated visible tab"
        finally:
            row.unbind_item()

    _run_ui(scenario)


def test_b2_reorder_requests_core(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = demo_snapshot()

    async def scenario(app: _TabsTestApp) -> None:
        from aconsole.ui.views.tabs.sidebar import TabSidebar

        client, tabs = _model(snapshot)
        by_section: dict[tuple[object, object], list[TabView]] = {}
        for tab in sorted(snapshot.tabs, key=lambda item: item.order):
            by_section.setdefault((tab.agent, tab.account_id), []).append(tab)
        pair = next(items[:2] for items in by_section.values() if len(items) >= 2)
        source, target = pair
        requests: list[tuple[str, object]] = []
        sequence = 0

        async def fake_request(
            method: str,
            params: object,
            result_type: type[object],
            *,
            long: bool = False,
        ) -> EmptyResult:
            _ = result_type, long
            nonlocal sequence
            requests.append((method, params))
            assert method == "tab/reorder"
            assert isinstance(params, ReorderParams)
            current = client.store.snapshot
            assert current is not None
            changed_tab = next(tab for tab in current.tabs if tab.id == params.tab_id)
            updated_tab = msgspec.structs.replace(changed_tab, order=params.order)
            sequence += 1
            assert client.store.apply(
                EventEnvelope(
                    seq=sequence,
                    at=updated_tab.status_since,
                    event=TabUpdated(tab=updated_tab, changed=frozenset({"order"})),
                )
            )
            return EmptyResult()

        monkeypatch.setattr(client, "request", fake_request)
        sidebar = TabSidebar(tabs)
        assert app.window is not None
        app.window.set_content(sidebar)
        app.window.present()
        await _until(sidebar.get_mapped)
        assert sidebar.handle_drop(str(source.id), str(target.id), after=True)
        pending = sidebar.pending_reorder
        assert pending is not None
        await asyncio.wait_for(pending, timeout=_WAIT_SECONDS)
        assert requests
        assert all(method == "tab/reorder" for method, _params in requests)
        assert all(isinstance(params, ReorderParams) for _method, params in requests)
        final_snapshot = client.store.snapshot
        assert final_snapshot is not None
        final_tabs = sorted(final_snapshot.tabs, key=lambda tab: tab.order)
        final_ids = [tab.id for tab in final_tabs]
        final_orders = [tab.order for tab in final_tabs]
        assert len(final_ids) == len(set(final_ids)) == len(snapshot.tabs)
        assert set(final_ids) == {tab.id for tab in snapshot.tabs}
        assert len(final_orders) == len(set(final_orders))
        assert final_ids.index(target.id) < final_ids.index(source.id)

    _run_ui(scenario)


def test_b2_rename_requests_core(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = demo_snapshot()

    async def scenario(app: _TabsTestApp) -> None:
        from aconsole.ui.views.tabs.row import TabRow

        client, tabs = _model(snapshot)
        item = tabs.model.get_item(0)
        assert item is not None
        requests: list[tuple[str, object]] = []

        async def fake_request(
            method: str,
            params: object,
            result_type: type[object],
            *,
            long: bool = False,
        ) -> EmptyResult:
            _ = result_type, long
            requests.append((method, params))
            return EmptyResult()

        monkeypatch.setattr(client, "request", fake_request)
        row = TabRow(tabs)
        row.bind_item(item)
        assert app.window is not None
        app.window.set_content(row)
        app.window.present()
        await _until(row.get_mapped)
        try:
            row.title_label.start_editing()
            row.title_label.set_text("Renamed from the row")
            row.title_label.stop_editing(True)
            await _until(lambda: bool(requests))
        finally:
            row.unbind_item()
        assert len(requests) == 1
        method, params = requests[0]
        assert method == "tab/rename"
        assert isinstance(params, RenameParams)
        assert params.tab_id == item.tab_id
        assert params.title == "Renamed from the row"

    _run_ui(scenario)


def test_b2_empty_state() -> None:
    async def scenario(app: _TabsTestApp) -> None:
        from aconsole.ui.views.tabs.sidebar import TabSidebar

        _client, tabs = _model(empty_snapshot())
        sidebar = TabSidebar(tabs)
        assert app.window is not None
        app.window.set_content(sidebar)
        app.window.present()

        assert isinstance(sidebar, Gtk.Stack)
        assert sidebar.selection.get_n_items() == 0
        assert sidebar.get_visible_child() is sidebar.empty_page
        assert isinstance(sidebar.empty_page, Adw.StatusPage)
        assert sidebar.empty_page.has_css_class("compact")

    _run_ui(scenario)
