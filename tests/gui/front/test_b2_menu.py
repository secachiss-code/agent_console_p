"""B2 row menu actions and explicit close intent routing."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, override

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib  # noqa: E402

from aconsole.contracts.app import EmptyResult  # noqa: E402
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabItem, TabsViewModel  # noqa: E402
from aconsole.ui.views.tabs.row import TabRow  # noqa: E402
from aconsole.ui.views.window import MainWindow  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import pytest


_SCENARIO_TIMEOUT_SECONDS = 5.0


class _MenuTestApp(Adw.Application):
    """Run GTK assertions on the application's supported event loop."""

    def __init__(self, scenario: Callable[[_MenuTestApp], Awaitable[None]]) -> None:
        super().__init__(
            application_id="io.github.aconsole.B2MenuTest",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.scenario = scenario
        self.client = CoreClient("unused")
        self.window: MainWindow | None = None
        self.error: Exception | None = None

    @override
    def do_activate(self) -> None:
        self.hold()
        _ = spawn_task(self._run_scenario())

    async def _run_scenario(self) -> None:
        try:
            await asyncio.wait_for(self.scenario(self), timeout=_SCENARIO_TIMEOUT_SECONDS)
        except Exception as error:  # noqa: BLE001 -- surface asynchronous GTK failures in pytest.
            self.error = error
        finally:
            if self.window is not None:
                self.window.close()
            self.release()
            self.quit()


def _run_ui(scenario: Callable[[_MenuTestApp], Awaitable[None]]) -> None:
    app = _MenuTestApp(scenario)
    assert run_app(lambda: app, ["aconsole-b2-menu-test"]) == 0
    assert app.error is None, repr(app.error)


def _window(app: _MenuTestApp) -> tuple[MainWindow, TabsViewModel, list[TabItem]]:
    app.client.store.apply_snapshot(demo_snapshot(), 0)
    tabs = TabsViewModel(app.client)
    rows = [tabs.model.get_item(index) for index in range(tabs.model.get_n_items())]
    items = [item for item in rows if isinstance(item, TabItem)]
    assert len(items) >= 2
    window = MainWindow(app, tabs)
    app.window = window
    return window, tabs, items


def _close_target(row: TabRow) -> GLib.Variant:
    model = row.menu_button.get_menu_model()
    assert model is not None
    string_type = GLib.VariantType.new("s")
    for index in range(model.get_n_items()):
        action = model.get_item_attribute_value(index, "action", GLib.VariantType.new("s"))
        target = model.get_item_attribute_value(index, "target", string_type)
        if action is not None and action.get_string() == "win.request-close-tab":
            assert target is not None
            return target
    message = "row menu has no explicit close-tab action target"
    raise AssertionError(message)


def test_b2_row_menu_rename_starts_editing_the_bound_row() -> None:
    async def scenario(app: _MenuTestApp) -> None:
        window, tabs, items = _window(app)
        row = TabRow(tabs)
        row.bind_item(items[1])
        window.set_content(row)
        window.present()
        try:
            assert row.menu_button.get_visible()
            assert row.activate_action("tab-row.rename", None)
            assert row.title_label.get_editing()
            assert row.tab_id == items[1].tab_id
        finally:
            row.unbind_item()

    _run_ui(scenario)


def test_b2_context_menu_opens_from_shift_f10() -> None:
    async def scenario(app: _MenuTestApp) -> None:
        window, tabs, items = _window(app)
        row = TabRow(tabs)
        row.bind_item(items[1])
        window.set_content(row)
        window.present()
        try:
            await asyncio.sleep(0.05)
            assert row.get_mapped()
            assert not row.menu_button.get_active()
            popover = row.menu_button.get_popover()
            assert popover is not None
            assert not popover.get_visible()

            assert not row.context_keys.emit(
                "key-pressed", Gdk.KEY_a, 0, Gdk.ModifierType(0)
            )
            assert row.context_keys.emit(
                "key-pressed", Gdk.KEY_F10, 0, Gdk.ModifierType.SHIFT_MASK
            )
            assert row.menu_button.get_active()
            assert popover.get_visible()
        finally:
            row.unbind_item()

    _run_ui(scenario)


def test_b2_close_intent_is_disabled_without_handler_and_targets_bound_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario(app: _MenuTestApp) -> None:
        window, tabs, items = _window(app)
        row = TabRow(tabs)
        row.bind_item(items[1])
        window.set_content(row)
        window.present()
        action = window.lookup_action("request-close-tab")
        assert action is not None
        assert action.get_enabled()  # a real window closes tabs through the core
        window.set_close_tab_handler(None)
        assert not action.get_enabled()

        calls: list[str] = []
        requests: list[str] = []

        async def unexpected_request(
            method: str,
            params: object,
            result_type: type[object],
        ) -> object:
            _ = params, result_type
            requests.append(method)
            if method == "tab/focus":
                return EmptyResult()
            message = f"unexpected core request: {method}"
            raise AssertionError(message)

        monkeypatch.setattr(app.client, "request", unexpected_request)
        window.set_close_tab_handler(calls.append)
        assert action.get_enabled()
        selected_index = next(
            index
            for index in range(window.selection.get_n_items())
            if window.selection.get_item(index) is items[0]
        )
        window.selection.set_selected(selected_index)
        assert window.selection.get_selected_item() is items[0]
        assert _close_target(row).get_string() == str(items[1].tab_id)
        assert window.activate_action("win.request-close-tab", _close_target(row))
        await asyncio.sleep(0.05)
        assert calls == [str(items[1].tab_id)]
        assert calls[0] != str(items[0].tab_id)
        assert "tab/close" not in requests
        row.unbind_item()

    _run_ui(scenario)


def test_b2_unbind_rebind_closes_menu_and_discards_old_row_target() -> None:
    async def scenario(app: _MenuTestApp) -> None:
        window, tabs, items = _window(app)
        calls: list[str] = []
        window.set_close_tab_handler(calls.append)
        row = TabRow(tabs)
        row.bind_item(items[0])
        window.set_content(row)
        window.present()
        try:
            row.menu_button.set_active(True)
            assert row.menu_button.get_popover() is not None
            row.unbind_item()
            assert not row.menu_button.get_active()
            assert row.menu_button.get_menu_model() is None

            row.bind_item(items[1])
            assert row.tab_id == str(items[1].tab_id)
            assert row.menu_button.get_menu_model() is not None
            assert _close_target(row).get_string() == str(items[1].tab_id)
            assert window.activate_action("win.request-close-tab", _close_target(row))
            assert calls == [str(items[1].tab_id)]
            assert str(items[0].tab_id) not in calls
        finally:
            row.unbind_item()

    _run_ui(scenario)
