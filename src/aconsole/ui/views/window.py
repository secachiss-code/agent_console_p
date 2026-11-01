"""Pilot window: demo tab titles and an empty content pane."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from aconsole.contracts.app import EmptyParams  # noqa: E402
from aconsole.contracts.common import PlanId, TabId  # noqa: E402
from aconsole.contracts.events import SettingsChanged, UiEvent  # noqa: E402
from aconsole.contracts.settings import Settings  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.client.store import CoreStore  # noqa: E402
from aconsole.ui.viewmodels.app_vm import AppViewModel  # noqa: E402
from aconsole.ui.viewmodels.logic.quota_fmt import quota_level  # noqa: E402
from aconsole.ui.viewmodels.sidebar_vm import SidebarSummaryViewModel  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabItem, TabsViewModel  # noqa: E402
from aconsole.ui.views.shortcuts import ShortcutManager  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402
from aconsole.ui.views.sidebar.plan_card import PlanCard, create_plan_placeholder  # noqa: E402
from aconsole.ui.views.sidebar.quota_card import QuotaCard  # noqa: E402
from aconsole.ui.views.tabs.sidebar import TabSidebar  # noqa: E402
from aconsole.ui.views.terminal.messages import _ as terminal_text  # noqa: E402
from aconsole.ui.views.terminal.page import TerminalPage  # noqa: E402

_LOG = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, tabs: TabsViewModel) -> None:
        super().__init__(application=app, title="AConsole")
        self.set_default_size(1280, 800)
        self.set_size_request(360, 360)
        self.tabs: TabsViewModel = tabs
        self._close_tab_handler: Callable[[str], None] | None = None
        self._snapshot_mode: bool = getattr(app, "snapshot_request", None) is not None
        self._init_shortcuts(app)
        self.split: Adw.OverlaySplitView = Adw.OverlaySplitView(
            min_sidebar_width=220, max_sidebar_width=320, sidebar_width_fraction=0.25,
        )
        self.split.add_css_class("ac-split")
        sidebar = Adw.ToolbarView()
        side_header = Adw.HeaderBar()
        side_header.set_show_end_title_buttons(False)
        _ = self.split.bind_property(
            "collapsed", side_header, "show-start-title-buttons",
            GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.INVERT_BOOLEAN,
        )
        sidebar.add_top_bar(side_header)
        self.tabs_sidebar: TabSidebar = TabSidebar(tabs)
        self.selection: Gtk.SingleSelection = self.tabs_sidebar.selection
        self.tab_list: Gtk.ListView = self.tabs_sidebar.tab_list
        _ = self.connect("notify::is-active", self._focus_changed)
        sidebar.set_content(self._build_summary())
        self.split.set_sidebar(sidebar)
        content = Adw.ToolbarView()
        self.content_view: Adw.ToolbarView = content
        header = Adw.HeaderBar()
        self.sidebar_button: Gtk.Button = Gtk.Button(icon_name="sidebar-show-symbolic")
        self.sidebar_button.set_action_name("win.toggle-sidebar")
        header.pack_start(self.sidebar_button)
        _ = self.split.bind_property(
            "collapsed", self.sidebar_button, "visible", GObject.BindingFlags.SYNC_CREATE,
        )
        self._install_tab_actions()
        header.set_show_start_title_buttons(False)
        _ = self.split.bind_property(
            "collapsed", header, "show-start-title-buttons", GObject.BindingFlags.SYNC_CREATE,
        )
        content.add_top_bar(header)
        self._install_quota_banner(content)
        self._setup_terminals(content)
        _ = self.selection.connect("notify::selected-item", self._selection_changed)
        _ = self.connect("unrealize", self._dispose_terminals)
        self.toast_overlay: Adw.ToastOverlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(content)
        self.tabs_sidebar.on_error = self._tab_error
        self.split.set_content(self.toast_overlay)
        self.set_content(self.split)
        self._summary_vm.on_changed = self._update_summary
        self._update_summary()
        self._show_selected_terminal()
        self._install_breakpoint(app)

    def _install_breakpoint(self, app: Adw.Application) -> None:
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
        narrow.add_setter(self.split, "collapsed", True)  # noqa: FBT003 -- GI setter takes a positional GValue
        # Stub: snapshot_request is a test seam, not a field of Adw.Application.
        request: object = getattr(app, "snapshot_request", None)
        view: object = getattr(request, "view", None)
        if view == "tabs":
            self.split.set_pin_sidebar(True)
        narrow.add_setter(self.split, "show-sidebar", view == "tabs")
        self.add_breakpoint(narrow)

    def _build_summary(self) -> Gtk.Box:
        self._summary_vm: SidebarSummaryViewModel = SidebarSummaryViewModel(self.tabs.client)
        self.plan_placeholder: Adw.StatusPage | None = None
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.tabs_sidebar.set_vexpand(True)
        body.append(self.tabs_sidebar)
        self.summary_card: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.summary_card.add_css_class("ac-summary-card")
        self.plan_card: PlanCard = PlanCard(on_activate=self._open_plan, on_tab=self._select_tab)
        self.quota_card: QuotaCard = QuotaCard()
        self.summary_card.append(self.plan_card)
        self.summary_card.append(Gtk.Separator())
        self.summary_card.append(self.quota_card)
        body.append(self.summary_card)
        return body

    def _setup_terminals(self, content: Adw.ToolbarView) -> None:
        self._terminals_disposed: bool = False
        self.terminal_pages: dict[str, TerminalPage] = {}
        self.terminal_stack: Gtk.Stack = Gtk.Stack()
        self.terminal_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        _ = self.terminal_stack.add_named(Adw.StatusPage(title=terminal_text("terminal.empty")), "empty")
        content.set_content(self.terminal_stack)

    def _show_selected_terminal(self, *, show_content: bool = True) -> None:
        if self._terminals_disposed:
            return
        selected = self.selection.get_selected_item()
        if not isinstance(selected, TabItem):
            self.terminal_stack.set_visible_child_name("empty")
            return
        tab_id = str(selected.tab_id)
        page = self.terminal_pages.get(tab_id)
        if page is None:
            page = TerminalPage(self.tabs.client, TabId(tab_id), self._terminal_new, self._terminal_close)
            self.terminal_pages[tab_id] = page
            _ = self.terminal_stack.add_named(page, tab_id)
        self.terminal_stack.set_visible_child(page)
        if show_content and self.content_view.get_content() is not self.terminal_stack:
            self.content_view.set_content(self.terminal_stack)
        page.start()

    def _terminal_new(self) -> None:
        self.app_vm.dispatch("new-tab")

    def _select_tab(self, tab_id: TabId) -> None:
        for index in range(self.selection.get_n_items()):
            item = self.selection.get_item(index)
            if isinstance(item, TabItem) and str(item.tab_id) == str(tab_id):
                self.selection.set_selected(index)
                break

    def _terminal_close(self, tab_id: str) -> None:
        if self._close_tab_handler is not None:
            self._close_tab_handler(tab_id)

    def _sync_terminal_pages(self, store: CoreStore) -> None:
        if self._terminals_disposed or store.reconnecting or store.snapshot is None:
            return
        present = {str(tab.id) for tab in store.snapshot.tabs}
        for tab_id in tuple(self.terminal_pages):
            if tab_id not in present:
                page = self.terminal_pages.pop(tab_id)
                page.close()
                self.terminal_stack.remove(page)
        self._show_selected_terminal(show_content=False)

    def _dispose_terminals(self, _widget: Gtk.Widget) -> None:
        self._terminals_disposed = True
        for page in self.terminal_pages.values():
            page.close()

    def _install_quota_banner(self, content: Adw.ToolbarView) -> None:
        self.quota_banner: Adw.Banner = Adw.Banner(title=translate("quota.critical"))
        self.quota_banner.set_button_label(translate("quota.details"))
        _ = self.quota_banner.connect("button-clicked", self._quota_details)
        content.add_top_bar(self.quota_banner)

    def _quota_details(self, _banner: Adw.Banner) -> None:
        _ = self.quota_card.open_details(self)

    def _update_summary(self) -> None:
        self.plan_card.set_plan(self._summary_vm.plan)
        self.quota_card.tabs = self._summary_vm.tabs
        self.quota_card.set_accounts(self._summary_vm.accounts)
        self.quota_banner.set_revealed(any(
            quota_level(window.used_pct) == "critical"
            for account in self._summary_vm.accounts for window in account.quota
        ))

    def _open_plan(self, plan_id: PlanId) -> None:
        plan = self._summary_vm.plan
        if plan is not None and plan.id == plan_id:
            self.plan_placeholder = create_plan_placeholder(plan)
            self.content_view.set_content(self.plan_placeholder)

    def _install_tab_actions(self) -> None:
        toggle = Gio.SimpleAction.new("toggle-sidebar", None)
        _ = toggle.connect("activate", self._toggle_sidebar)
        self.add_action(toggle)
        new_tab = Gio.SimpleAction.new("new-tab", None)
        _ = new_tab.connect("activate", self._new_tab)
        self.add_action(new_tab)
        self.close_tab_action: Gio.SimpleAction = Gio.SimpleAction.new("request-close-tab", GLib.VariantType.new("s"))
        self.close_tab_action.set_enabled(False)
        _ = self.close_tab_action.connect("activate", self._request_close_tab)
        self.add_action(self.close_tab_action)

    def _init_shortcuts(self, app: Adw.Application) -> None:
        client = getattr(app, "client", None)
        if not isinstance(client, CoreClient):
            message = "application has no core client"
            raise TypeError(message)
        self.app_vm: AppViewModel = AppViewModel(client)
        self._settings_revision: int = -1
        self.app_vm.action_handler = self._handle_action
        self.shortcut_manager: ShortcutManager = ShortcutManager(self, self.app_vm)
        self.app_vm.client.store.subscribe(self._store_event)
        self.app_vm.client.store.subscribe_snapshot(self._snapshot_changed)
        _ = spawn_task(self._refresh_settings())

    def _toggle_sidebar(self, _action: Gio.SimpleAction, _parameter: object) -> None:
        self.app_vm.dispatch("toggle-sidebar")

    def _new_tab(self, _action: Gio.SimpleAction, _parameter: object) -> None:
        self.app_vm.dispatch("new-tab")

    def _tab_error(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast.new(message))

    def set_close_tab_handler(self, callback: Callable[[str], None] | None) -> None:
        self._close_tab_handler = callback
        self.close_tab_action.set_enabled(callback is not None)

    def _request_close_tab(self, _action: Gio.SimpleAction, parameter: GLib.Variant | None) -> None:
        if parameter is not None and self._close_tab_handler is not None:
            self._close_tab_handler(parameter.get_string())

    def _selection_changed(self, _object: object, _property: object) -> None:
        self._show_selected_terminal()
        self._focus_changed(_object, _property)

    def _focus_changed(self, _object: object, _property: object) -> None:
        if self._snapshot_mode:
            return
        selected = self.selection.get_selected_item()
        tab_id = str(selected.tab_id) if isinstance(selected, TabItem) else None
        _ = spawn_task(self._report_focus(tab_id, active=self.is_active()))

    async def _report_focus(self, tab_id: str | None, *, active: bool) -> None:
        try:
            await self.tabs.focus(tab_id, window_active=active)
        except Exception:  # noqa: BLE001 -- focus reports must not break the window on disconnect
            _LOG.debug("Could not report tab focus", exc_info=True)

    def _handle_action(self, action_id: str) -> None:
        if action_id == "toggle-sidebar":
            self.split.set_show_sidebar(not self.split.get_show_sidebar())

    def _store_event(self, event: UiEvent, _store: CoreStore) -> None:
        self._sync_terminal_pages(_store)
        if isinstance(event, SettingsChanged):
            _ = spawn_task(self._refresh_settings())

    def _snapshot_changed(self, _store: CoreStore) -> None:
        self._sync_terminal_pages(_store)
        _ = spawn_task(self._refresh_settings())

    async def _refresh_settings(self) -> None:
        try:
            settings = await self.app_vm.client.request("settings/get", EmptyParams(), Settings)
        except Exception:  # noqa: BLE001 -- preserve the last working keymap when the core is unavailable
            _LOG.debug("Could not refresh keyboard settings", exc_info=True)
            return
        if settings.rev < self._settings_revision:
            return
        self._settings_revision = settings.rev
        _ = self.app_vm.apply_overrides(settings.keymap_overrides)
