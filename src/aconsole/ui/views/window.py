"""Pilot window: demo tab titles and an empty content pane."""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GObject, Gtk, Pango  # noqa: E402

from aconsole.contracts.app import EmptyParams  # noqa: E402
from aconsole.contracts.events import SettingsChanged, UiEvent  # noqa: E402
from aconsole.contracts.settings import Settings  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.client.store import CoreStore  # noqa: E402
from aconsole.ui.viewmodels.app_vm import AppViewModel  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabItem, TabsViewModel  # noqa: E402
from aconsole.ui.views.shortcuts import ShortcutManager  # noqa: E402

_LOG = logging.getLogger(__name__)


def _setup(_factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
    label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
    label.add_css_class("ac-tab-title")
    item.set_child(label)


def _bind(_factory: Gtk.SignalListItemFactory, item: Gtk.ListItem) -> None:
    row = item.get_item()
    label = item.get_child()
    if isinstance(row, TabItem) and isinstance(label, Gtk.Label):
        title = row.title
        if isinstance(title, str):
            label.set_label(title)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, tabs: TabsViewModel) -> None:
        super().__init__(application=app, title="AConsole")
        self.set_default_size(1280, 800)
        self.set_size_request(360, 360)
        self.tabs: TabsViewModel = tabs
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
        factory = Gtk.SignalListItemFactory()
        _ = factory.connect("setup", _setup)
        _ = factory.connect("bind", _bind)
        self.selection: Gtk.SingleSelection = Gtk.SingleSelection.new(tabs.model)
        self.tab_list: Gtk.ListView = Gtk.ListView(model=self.selection, factory=factory)
        self.tab_list.add_css_class("navigation-sidebar")
        self.tab_list.add_css_class("ac-tabs")
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(self.tab_list)
        sidebar.set_content(scroll)
        self.split.set_sidebar(sidebar)
        content = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.sidebar_button: Gtk.Button = Gtk.Button(icon_name="sidebar-show-symbolic")
        self.sidebar_button.set_action_name("win.toggle-sidebar")
        header.pack_start(self.sidebar_button)
        _ = self.split.bind_property(
            "collapsed", self.sidebar_button, "visible", GObject.BindingFlags.SYNC_CREATE,
        )
        toggle = Gio.SimpleAction.new("toggle-sidebar", None)
        _ = toggle.connect("activate", self._toggle_sidebar)
        self.add_action(toggle)
        header.set_show_start_title_buttons(False)
        _ = self.split.bind_property(
            "collapsed", header, "show-start-title-buttons", GObject.BindingFlags.SYNC_CREATE,
        )
        content.add_top_bar(header)
        content.set_content(Adw.StatusPage(title="AConsole"))
        self.split.set_content(content)
        self.set_content(self.split)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
        narrow.add_setter(self.split, "collapsed", True)  # noqa: FBT003 -- GI setter takes a positional GValue
        narrow.add_setter(self.split, "show-sidebar", False)  # noqa: FBT003 -- GI setter takes a positional GValue
        self.add_breakpoint(narrow)

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

    def _handle_action(self, action_id: str) -> None:
        if action_id == "toggle-sidebar":
            self.split.set_show_sidebar(not self.split.get_show_sidebar())

    def _store_event(self, event: UiEvent, _store: CoreStore) -> None:
        if isinstance(event, SettingsChanged):
            _ = spawn_task(self._refresh_settings())

    def _snapshot_changed(self, _store: CoreStore) -> None:
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
