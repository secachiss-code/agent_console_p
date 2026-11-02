"""Pilot window: demo tab titles and an empty content pane."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from aconsole.contracts.app import CommandResult, EmptyParams, PaletteQuery  # noqa: E402
from aconsole.contracts.common import PlanId, TabId  # noqa: E402
from aconsole.contracts.events import SettingsChanged, UiEvent  # noqa: E402
from aconsole.contracts.settings import Settings  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.client.store import CoreStore  # noqa: E402
from aconsole.ui.viewmodels.app_vm import AppViewModel  # noqa: E402
from aconsole.ui.viewmodels.logic.quota_fmt import quota_level  # noqa: E402
from aconsole.ui.viewmodels.setup_vm import PreferencesViewModel  # noqa: E402
from aconsole.ui.viewmodels.sidebar_vm import SidebarSummaryViewModel  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabItem, TabsViewModel  # noqa: E402
from aconsole.ui.views.palette.dialog import PaletteDialog  # noqa: E402
from aconsole.ui.views.palette.messages import _ as palette_text  # noqa: E402
from aconsole.ui.views.setup.messages import _ as setup_text  # noqa: E402
from aconsole.ui.views.setup.preferences import PreferencesDialog  # noqa: E402
from aconsole.ui.views.setup.wizard import SetupWizard  # noqa: E402
from aconsole.ui.views.shortcuts import ShortcutManager  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402
from aconsole.ui.views.sidebar.plan_card import PlanCard  # noqa: E402
from aconsole.ui.views.sidebar.plan_graph import PlanGraph  # noqa: E402
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
        sidebar.add_top_bar(self._build_side_header())
        self.tabs_sidebar: TabSidebar = TabSidebar(tabs)
        self.selection: Gtk.SingleSelection = self.tabs_sidebar.selection
        self.tab_list: Gtk.ListView = self.tabs_sidebar.tab_list
        self.tabs_sidebar.on_row_pressed = self._show_selected_terminal
        _ = self.connect("notify::is-active", self._focus_changed)
        sidebar.set_content(self._build_summary())
        self.split.set_sidebar(sidebar)
        content = Adw.ToolbarView()
        self.content_view: Adw.ToolbarView = content
        header = Adw.HeaderBar()
        self.content_header: Adw.HeaderBar = header
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=self._settings_menu))
        self.sidebar_button: Gtk.Button = Gtk.Button(icon_name="sidebar-show-symbolic")
        self.sidebar_button.set_action_name("win.toggle-sidebar")
        header.pack_start(self.sidebar_button)
        _ = self.split.bind_property(
            "collapsed", self.sidebar_button, "visible", GObject.BindingFlags.SYNC_CREATE,
        )
        self._install_tab_actions()
        _ = self.split.bind_property("collapsed", header, "visible", GObject.BindingFlags.SYNC_CREATE)
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
        _ = self.connect("map", self._maybe_setup)

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

    def _build_side_header(self) -> Adw.HeaderBar:
        """The only title bar of a wide window: window buttons in one island, then the application name."""
        self._settings_menu: Gio.Menu = self._build_settings_menu()
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        self.window_island: Gtk.Box = Gtk.Box(spacing=2)
        self.window_island.add_css_class("ac-window-island")
        self.window_island.append(Gtk.WindowControls(side=Gtk.PackType.START))
        self.window_island.append(Gtk.WindowControls(side=Gtk.PackType.END))
        menu = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=self._settings_menu)
        menu.set_tooltip_text(setup_text("prefs.title"))
        self.window_island.append(menu)
        _ = self.split.bind_property(
            "collapsed", self.window_island, "visible",
            GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.INVERT_BOOLEAN,
        )
        header.pack_start(self.window_island)
        title = Gtk.Label(label="AConsole")
        title.add_css_class("ac-app-title")
        header.set_title_widget(title)
        return header

    def _build_summary(self) -> Gtk.Box:
        self._summary_vm: SidebarSummaryViewModel = SidebarSummaryViewModel(self.tabs.client)
        self.plan_page: PlanGraph | None = None
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.tabs_sidebar.set_vexpand(True)
        body.append(self.tabs_sidebar)
        self.summary_card: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.summary_card.add_css_class("ac-summary-card")
        self.plan_card: PlanCard = PlanCard(on_activate=self._open_plan)
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
        if show_content and self.content_view.get_content() is not self.terminal_stack:
            self.plan_page = None
            self.content_view.set_content(self.terminal_stack)
        selected = self.selection.get_selected_item()
        if not isinstance(selected, TabItem):
            self.terminal_stack.set_visible_child_name("empty")
            return
        tab_id = str(selected.tab_id)
        page = self.terminal_pages.get(tab_id)
        if page is None:
            page = TerminalPage(self.tabs.client, TabId(tab_id), self._terminal_new, self._terminal_close)
            if self._terminal_settings is not None:
                page.apply_settings(self._terminal_settings)
            self.terminal_pages[tab_id] = page
            _ = self.terminal_stack.add_named(page, tab_id)
        self.terminal_stack.set_visible_child(page)
        page.start()

    def _terminal_new(self) -> None:
        self.app_vm.dispatch("new-tab")

    def _select_tab(self, tab_id: TabId) -> None:
        for index in range(self.selection.get_n_items()):
            item = self.selection.get_item(index)
            if isinstance(item, TabItem) and str(item.tab_id) == str(tab_id):
                self.selection.set_selected(index)
                break
        self._show_selected_terminal()

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
        self._preferences_unsubscribe()
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
        plan = self._summary_vm.plan
        self.plan_card.set_plan(plan)
        if self.plan_page is not None and plan != self.plan_page.plan:
            if plan is not None and plan.id == self.plan_page.plan.id:
                self._open_plan(plan.id)
            else:
                self._show_selected_terminal()
        self.quota_card.tabs = self._summary_vm.tabs
        self.quota_card.set_accounts(self._summary_vm.accounts)
        self.quota_banner.set_revealed(any(
            quota_level(window.used_pct) == "critical"
            for account in self._summary_vm.accounts for window in account.quota
        ))

    def _open_plan(self, plan_id: PlanId) -> None:
        plan = self._summary_vm.plan
        if plan is not None and plan.id == plan_id:
            self.plan_page = PlanGraph(plan, self._close_plan, self._select_tab)
            self.content_view.set_content(self.plan_page)

    def _close_plan(self) -> None:
        self._show_selected_terminal()

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
        self.palette_dialog: PaletteDialog | None = None
        client = getattr(app, "client", None)
        if not isinstance(client, CoreClient):
            message = "application has no core client"
            raise TypeError(message)
        self.app_vm: AppViewModel = AppViewModel(client)
        self._settings_revision: int = -1
        self._terminal_settings: Settings | None = None
        self.preferences_vm: PreferencesViewModel = PreferencesViewModel(client)
        self._preferences_unsubscribe: Callable[[], None] = self.preferences_vm.subscribe(self._apply_preferences)
        self.preferences_dialog: PreferencesDialog | None = None
        self.setup_wizard: SetupWizard | None = None
        self._setup_shown: bool = False
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
        elif action_id == "palette":
            self._open_palette()
        elif action_id == "settings":
            self._open_preferences()

    def _build_settings_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        for name, label in (
            ("palette", palette_text("palette.title")),
            ("settings", setup_text("prefs.title")),
            ("setup", setup_text("setup.restart")),
        ):
            action = Gio.SimpleAction.new(name, None)
            _ = action.connect("activate", self._settings_menu_action)
            self.add_action(action)
            menu.append(label, f"win.{name}")
        return menu

    def _settings_menu_action(self, action: Gio.SimpleAction, _parameter: object) -> None:
        if action.get_name() == "setup":
            _ = spawn_task(self._restart_setup())
        else:
            self.app_vm.dispatch(action.get_name())

    def _open_preferences(self) -> None:
        if self.preferences_dialog is None:
            self.preferences_dialog = PreferencesDialog(self.preferences_vm)
        self.preferences_dialog.open(self)

    def show_setup(self) -> None:
        self._setup_shown = True
        if self.setup_wizard is None:
            self.setup_wizard = SetupWizard(self.tabs.client, self.preferences_vm, self._select_tab)
        self.setup_wizard.open(self)

    async def _restart_setup(self) -> None:
        from aconsole.contracts.settings import SettingsPatch  # noqa: PLC0415

        try:
            _ = await self.preferences_vm.update(SettingsPatch(setup_completed=False))
            if not self._terminals_disposed:
                self.show_setup()
        except Exception:  # noqa: BLE001 -- failure to persist rerun state must remain visible
            if not self._terminals_disposed:
                self.toast_overlay.add_toast(Adw.Toast.new(setup_text("setup.error")))

    def _maybe_setup(self, _widget: Gtk.Widget) -> None:
        snapshot = self.tabs.client.store.snapshot
        if not self._snapshot_mode and not self._setup_shown and snapshot is not None and snapshot.setup.required:
            self.show_setup()

    def _open_palette(self) -> None:
        if self.palette_dialog is None:
            self.palette_dialog = PaletteDialog(self.tabs.client, self._palette_result)
        selected = self.selection.get_selected_item()
        snapshot = self.tabs.client.store.snapshot
        context = PaletteQuery(
            tab_id=TabId(str(selected.tab_id)) if isinstance(selected, TabItem) else None,
            project_id=snapshot.active_project if snapshot is not None else None,
        )
        self.palette_dialog.open(context, self)

    def _palette_result(self, result: CommandResult) -> None:
        if result.toast:
            self.toast_overlay.add_toast(Adw.Toast.new(result.toast))
        if not result.ok:
            return
        if result.opened_tab is not None:
            self._select_tab(result.opened_tab)
        if result.open_page is not None and result.open_page.kind == "plan":
            self._open_plan(PlanId(result.open_page.ref))
        if result.open_uri is not None:
            launcher = Gtk.UriLauncher.new(result.open_uri)
            launcher.launch(self, None, self._uri_opened)

    def _uri_opened(self, launcher: Gtk.UriLauncher, result: Gio.AsyncResult) -> None:
        try:
            _ = launcher.launch_finish(result)
        except GLib.Error:
            self.toast_overlay.add_toast(Adw.Toast.new(palette_text("palette.uri_error")))

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
        self.preferences_vm.accept(settings)

    def _apply_preferences(self, settings: Settings) -> None:
        if self._terminals_disposed or settings.rev < self._settings_revision:
            return
        self._settings_revision = settings.rev
        self._terminal_settings = settings
        _ = self.app_vm.apply_overrides(settings.keymap_overrides)
        for page in self.terminal_pages.values():
            page.apply_settings(settings)
        if not self._snapshot_mode:
            schemes = {"system": Adw.ColorScheme.DEFAULT, "light": Adw.ColorScheme.FORCE_LIGHT,
                       "dark": Adw.ColorScheme.FORCE_DARK}
            Adw.StyleManager.get_default().set_color_scheme(schemes[settings.theme])
