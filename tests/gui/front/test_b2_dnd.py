"""B2 GTK drag-and-drop signal wiring without synthetic pointer input."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, override

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from aconsole.contracts.app import EmptyResult, ReorderParams  # noqa: E402
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabItem, TabsViewModel  # noqa: E402
from aconsole.ui.views.tabs.row import TabRow  # noqa: E402
from aconsole.ui.views.tabs.sidebar import TabSidebar  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import pytest


_WAIT_SECONDS = 2.0


class _DndTestApp(Adw.Application):
    def __init__(self, scenario: Callable[[_DndTestApp], Awaitable[None]]) -> None:
        super().__init__(
            application_id="io.github.aconsole.B2DndTest",
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
            await asyncio.wait_for(self.scenario(self), timeout=5.0)
        except Exception as error:  # noqa: BLE001 -- report async GTK failures to pytest.
            self.error = error
        finally:
            if self.window is not None:
                self.window.close()
            self.release()
            self.quit()


def _run_ui(scenario: Callable[[_DndTestApp], Awaitable[None]]) -> None:
    app = _DndTestApp(scenario)
    assert run_app(lambda: app, ["aconsole-b2-dnd-test"]) == 0
    assert app.error is None, repr(app.error)


async def _until(predicate: Callable[[], bool]) -> None:
    async def poll() -> None:
        while not predicate():  # noqa: ASYNC110 -- GTK mapping has no awaitable notification.
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout=_WAIT_SECONDS)


def _walk(widget: Gtk.Widget) -> list[Gtk.Widget]:
    found = [widget]
    child = widget.get_first_child()
    while child is not None:
        found.extend(_walk(child))
        child = child.get_next_sibling()
    return found


def test_b2_drag_prepare_and_drop_signals_request_reorder(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = demo_snapshot()

    async def scenario(app: _DndTestApp) -> None:
        client = CoreClient("unused")
        client.store.apply_snapshot(snapshot, 0)
        tabs = TabsViewModel(client)
        sections: dict[str, list[TabItem]] = {}
        for index in range(tabs.model.get_n_items()):
            item = tabs.model.get_item(index)
            assert item is not None
            sections.setdefault(str(item.section_key), []).append(item)
        source, target = next(items[:2] for items in sections.values() if len(items) >= 2)
        requests: list[ReorderParams] = []

        async def fake_request(
            method: str,
            params: object,
            result_type: type[object],
            *,
            long: bool = False,
        ) -> EmptyResult:
            _ = result_type, long
            assert method == "tab/reorder"
            assert isinstance(params, ReorderParams)
            requests.append(params)
            return EmptyResult()

        monkeypatch.setattr(client, "request", fake_request)
        sidebar = TabSidebar(tabs)
        assert app.window is not None
        app.window.set_default_size(480, 600)
        app.window.set_content(sidebar)
        app.window.present()
        await _until(sidebar.get_mapped)

        bound_rows = {
            widget.tab_id: widget
            for widget in _walk(sidebar.tab_list)
            if isinstance(widget, TabRow) and widget.tab_id is not None
        }
        source_row = bound_rows[str(source.tab_id)]
        target_row = bound_rows[str(target.tab_id)]
        await _until(lambda: source_row.get_height() > 0 and target_row.get_height() > 0)

        source_controller = next(
            controller
            for controller in source_row.observe_controllers()
            if isinstance(controller, Gtk.DragSource)
        )
        target_controller = next(
            controller
            for controller in target_row.observe_controllers()
            if isinstance(controller, Gtk.DropTarget)
        )

        provider = source_controller.emit("prepare", 0.0, 0.0)
        assert isinstance(provider, Gdk.ContentProvider)
        accepted = target_controller.emit("drop", str(source.tab_id), 0.0, float(target_row.get_height()))
        assert accepted is True

        pending = sidebar.pending_reorder
        assert pending is not None
        await asyncio.wait_for(pending, timeout=_WAIT_SECONDS)
        assert requests
        assert any(params.tab_id == source.tab_id for params in requests)

    _run_ui(scenario)
