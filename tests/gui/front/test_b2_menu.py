"""B2 row menu actions and explicit close intent routing."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, override

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from aconsole.contracts.app import EmptyResult, HealthResult, PlanNodeDetailsView  # noqa: E402
from aconsole.contracts.events import ProjectsChanged  # noqa: E402
from aconsole.contracts.ipc import EventEnvelope  # noqa: E402
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabItem, TabsViewModel  # noqa: E402
from aconsole.ui.views.review.page import ReviewPage  # noqa: E402
from aconsole.ui.views.sidebar.plan_graph import PlanGraph  # noqa: E402
from aconsole.ui.views.sidebar.project_index import ProjectIndexPage  # noqa: E402
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
        self.scenario: Callable[[_MenuTestApp], Awaitable[None]] = scenario
        self.client: CoreClient = CoreClient("unused")
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


def test_header_session_search_filters_and_restores_focus() -> None:
    async def scenario(app: _MenuTestApp) -> None:
        window, _, _ = _window(app)
        window.present()
        await asyncio.sleep(0.05)
        count = window.selection.get_n_items()
        assert count > 0
        search = window.session_search
        button = window.session_search_button
        assert Gtk.test_accessible_has_property(search, Gtk.AccessibleProperty.LABEL)
        assert Gtk.test_accessible_has_property(button, Gtk.AccessibleProperty.LABEL)
        assert not search.get_visible()
        button.emit("clicked")
        assert search.get_visible()
        focus = window.get_focus()
        assert focus is not None
        assert focus.is_ancestor(search)
        search.set_text("no session matches this query")
        assert window.selection.get_n_items() == 0
        search.emit("stop-search")
        assert not search.get_visible()
        assert search.get_text() == ""
        assert window.selection.get_n_items() == count
        assert window.get_focus() is button

    _run_ui(scenario)


def test_project_navigation_opens_each_page_from_one_row() -> None:
    async def scenario(app: _MenuTestApp) -> None:
        window, _, _ = _window(app)
        window.present()
        selected = window.selection.get_selected_item()
        for button, page_type in (
            (window.plan_card, PlanGraph),
            (window.index_button, ProjectIndexPage),
            (window.git_button, ReviewPage),
        ):
            assert button.get_tooltip_text()
            assert Gtk.test_accessible_has_property(button, Gtk.AccessibleProperty.LABEL)
            button.emit("clicked")
            assert isinstance(window.content_view.get_content(), page_type)
            assert window.selection.get_selected_item() is selected
            assert button.has_css_class("suggested-action")
        assert window.review_page is not None
        window.review_page.stack.set_visible_child_name("changes")
        assert not window.git_button.has_css_class("suggested-action")
        assert window.tabs_sidebar.on_row_pressed is not None
        window.tabs_sidebar.on_row_pressed()
        assert window.content_view.get_content() is window.terminal_stack
        assert not any(
            button.has_css_class("suggested-action")
            for button in (window.plan_card, window.index_button, window.git_button)
        )

    _run_ui(scenario)


def test_project_index_back_keeps_the_plan_node() -> None:
    async def scenario(app: _MenuTestApp) -> None:
        window, _, _ = _window(app)
        window.present()
        window.plan_card.emit("clicked")
        plan = window.plan_page
        assert plan is not None
        node = plan.plan.nodes[0]
        plan.select_node(node.id)
        plan.set_node_details(node.id, PlanNodeDetailsView(lines=("Node details",), paths=("src/**",)))
        assert plan.index_button.get_visible()
        plan.index_button.emit("clicked")
        index = window.content_view.get_content()
        assert isinstance(index, ProjectIndexPage)
        assert window.index_button.has_css_class("suggested-action")
        index.back_button.emit("clicked")
        assert window.content_view.get_content() is plan
        assert plan.selected_node == node.id
        assert plan.index_button.get_visible()
        assert window.plan_card.has_css_class("suggested-action")
        snapshot = demo_snapshot()
        switched = ProjectsChanged(projects=snapshot.projects, active_project=snapshot.projects[1].id)
        assert app.client.store.apply(EventEnvelope(seq=1, at=plan.plan.updated_at, event=switched))
        assert window.content_view.get_content() is window.terminal_stack
        assert not window.plan_card.get_sensitive()

    _run_ui(scenario)


def test_settings_action_opens_modal_categories() -> None:
    async def scenario(app: _MenuTestApp) -> None:
        window, _, _ = _window(app)
        window.set_default_size(1280, 800)
        window.present()
        await asyncio.sleep(0.05)
        assert window.activate_action("win.settings", None)
        dialog = window.preferences_dialog
        assert dialog is not None
        try:
            await asyncio.sleep(0.35)
            body = dialog.get_child()
            assert isinstance(body, Adw.ToolbarView)
            layout = body.get_content()
            assert isinstance(layout, Adw.OverlaySplitView)
            categories = layout.get_sidebar()
            assert isinstance(categories, Gtk.StackSidebar)
            assert categories.get_mapped()
            focus = dialog.get_focus()
            if focus is None or not focus.is_ancestor(categories):
                assert categories.child_focus(Gtk.DirectionType.TAB_FORWARD)
            focus = dialog.get_focus()
            assert focus is not None
            assert focus.is_ancestor(categories)
            stack = categories.get_stack()
            assert stack is not None
            assert all(
                isinstance(stack.get_child_by_name(name), Adw.PreferencesPage)
                for name in ("appearance", "terminal", "tools_limits", "permissions", "mcp")
            )
            dialog.set_visible_page(dialog.mcp_page)
            assert stack.get_visible_child() is dialog.mcp_page
            assert dialog.save_button.get_visible()
        finally:
            dialog.force_close()

    _run_ui(scenario)


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

            assert not row.context_keys.emit("key-pressed", Gdk.KEY_a, 0, Gdk.ModifierType(0))
            assert row.context_keys.emit("key-pressed", Gdk.KEY_F10, 0, Gdk.ModifierType.SHIFT_MASK)
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
            index for index in range(window.selection.get_n_items()) if window.selection.get_item(index) is items[0]
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


def test_the_menu_opens_the_health_dialog_with_the_core_s_answer() -> None:
    answer = HealthResult(
        database=True,
        writer_queued=7,
        writer_failed=0,
        writer_last_commit_ms=1.0,
        ipc_answered=9,
        ipc_slowest_ms=2.0,
    )

    async def scenario(app: _MenuTestApp) -> None:
        window, _tabs, _items = _window(app)

        async def request(method: str, _params: object, _result: object) -> HealthResult:
            assert method == "app/health"
            return answer

        app.client.request = request  # pyright: ignore[reportAttributeAccessIssue] -- the stub answers one method
        window.present()
        assert window.activate_action("win.health", None)
        dialog = window.health_dialog
        assert dialog is not None
        await dialog.refresh()
        assert dialog.values["health.queued"].get_label() == "7"
        assert dialog.values["health.answered"].get_label() == "9"

    _run_ui(scenario)
