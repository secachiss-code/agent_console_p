"""Pilot window: demo tab titles and an empty content pane."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Literal

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from aconsole.contracts.app import (  # noqa: E402
    CommandResult,
    EmptyParams,
    EmptyResult,
    PaletteQuery,
    PlanNodeView,
    QuitCheckResult,
    SetNodeStatusParams,
    TabIdParams,
    TabView,
)
from aconsole.contracts.common import ApprovalDecision, NodeId, NodeStatus, PlanId, ProjectId, TabId  # noqa: E402
from aconsole.contracts.events import CheckpointCreated, GitStateUpdated, InjectToTerminal, SettingsChanged, UiEvent  # noqa: E402
from aconsole.contracts.settings import Settings  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.client.store import CoreStore  # noqa: E402
from aconsole.ui.client.ui_state import UiState  # noqa: E402
from aconsole.ui.viewmodels.app_vm import AppViewModel  # noqa: E402
from aconsole.ui.viewmodels.approval_alerts import Alert, ApprovalAlerts  # noqa: E402
from aconsole.ui.viewmodels.approvals_vm import ApprovalsViewModel  # noqa: E402
from aconsole.ui.viewmodels.git_vm import GitViewModel, git_summary  # noqa: E402
from aconsole.ui.viewmodels.health_vm import HealthViewModel  # noqa: E402
from aconsole.ui.viewmodels.logic.fonts import ZOOM_DEFAULT, css_font_family, font_families, zoomed  # noqa: E402
from aconsole.ui.viewmodels.logic.quota_fmt import quota_level  # noqa: E402
from aconsole.ui.viewmodels.review_vm import ReviewViewModel  # noqa: E402
from aconsole.ui.viewmodels.setup_vm import PreferencesViewModel  # noqa: E402
from aconsole.ui.viewmodels.sidebar_vm import SidebarSummaryViewModel  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabItem, TabsViewModel  # noqa: E402
from aconsole.ui.views.approvals.alert import alert_notification  # noqa: E402
from aconsole.ui.views.approvals.panel import ApprovalPanel, place_at_bottom  # noqa: E402
from aconsole.ui.views.health_dialog import HealthDialog  # noqa: E402
from aconsole.ui.views.palette.dialog import PaletteDialog  # noqa: E402
from aconsole.ui.views.palette.messages import _ as palette_text  # noqa: E402
from aconsole.ui.views.review.git_pane import GitPane  # noqa: E402
from aconsole.ui.views.review.messages import _ as review_text  # noqa: E402
from aconsole.ui.views.review.page import ReviewPage  # noqa: E402
from aconsole.ui.views.setup.messages import _ as setup_text  # noqa: E402
from aconsole.ui.views.setup.preferences import PreferencesDialog  # noqa: E402
from aconsole.ui.views.setup.wizard import SetupWizard  # noqa: E402
from aconsole.ui.views.shortcuts import ShortcutManager  # noqa: E402
from aconsole.ui.views.shortcuts_dialog import ShortcutsDialog  # noqa: E402
from aconsole.ui.views.sidebar.git_card import GitCard  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402
from aconsole.ui.views.sidebar.plan_card import PlanCard  # noqa: E402
from aconsole.ui.views.sidebar.plan_graph import PlanGraph  # noqa: E402
from aconsole.ui.views.sidebar.quota_card import QuotaCard  # noqa: E402
from aconsole.ui.views.sidebar.section import SummarySection  # noqa: E402
from aconsole.ui.views.tabs.messages import _ as tabs_text  # noqa: E402
from aconsole.ui.views.tabs.new_tab import NewTabDialog  # noqa: E402
from aconsole.ui.views.tabs.project_bar import ProjectBar  # noqa: E402
from aconsole.ui.views.tabs.sidebar import TabSidebar  # noqa: E402
from aconsole.ui.views.terminal.messages import _ as terminal_text  # noqa: E402
from aconsole.ui.views.terminal.page import TerminalPage  # noqa: E402

_LOG = logging.getLogger(__name__)
_GIT_KEEP_S = 15
_ISLAND_MARGIN = 8
_TAB_ARRIVAL_MS = 200
_BANNER_HEIGHT = 48

if TYPE_CHECKING:
    from collections.abc import Callable


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, tabs: TabsViewModel) -> None:
        super().__init__(application=app, title="AConsole")
        self.set_default_size(1280, 800)
        self.set_size_request(360, 360)
        self.tabs: TabsViewModel = tabs
        self._close_tab_handler: Callable[[str], None] | None = self._close_tab
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
        self._install_approvals(content)
        _ = self.selection.connect("notify::selected-item", self._selection_changed)
        _ = self.connect("unrealize", self._dispose_terminals)
        self.toast_overlay: Adw.ToastOverlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self.approvals_overlay)
        self.tabs_sidebar.on_error = self._tab_error
        self.end_island: Gtk.Box = self._build_end_island()
        frame = Gtk.Overlay(child=self.toast_overlay)
        frame.add_overlay(self.end_island)
        self.split.set_content(frame)
        for source, name in (
            (self.split, "notify::collapsed"),
            (self.start_controls, "notify::empty"),
            (self.end_controls, "notify::empty"),
        ):
            _ = source.connect(name, self._sync_islands)
        self._sync_islands()
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
        self.start_controls: Gtk.WindowControls = Gtk.WindowControls(side=Gtk.PackType.START)
        self.start_island: Gtk.Box = self._island(self.start_controls)
        header.pack_start(self.start_island)
        menu = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=self._settings_menu)
        menu.set_tooltip_text(setup_text("prefs.title"))
        header.pack_end(menu)
        # New tab lives beside the menu: the list below it is then only tabs.
        self.new_tab_button: Gtk.Button = Gtk.Button(icon_name="list-add-symbolic", action_name="win.new-tab")
        self.new_tab_button.set_tooltip_text(tabs_text("tabs.new"))
        self.new_tab_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [GObject.Value(GObject.TYPE_STRING, tabs_text("tabs.new"))],
        )
        header.pack_end(self.new_tab_button)
        title = Gtk.Label(label="AConsole")
        title.add_css_class("ac-app-title")
        header.set_title_widget(title)
        return header

    @staticmethod
    def _island(controls: Gtk.WindowControls) -> Gtk.Box:
        island = Gtk.Box()
        island.add_css_class("ac-window-island")
        island.append(controls)
        return island

    def _build_end_island(self) -> Gtk.Box:
        """Buttons the system puts after the colon stay at the window's edge, floating over the full-height content."""
        self.end_controls: Gtk.WindowControls = Gtk.WindowControls(side=Gtk.PackType.END)
        island = self._island(self.end_controls)
        island.add_css_class("ac-window-island-floating")
        island.set_halign(Gtk.Align.END)
        island.set_valign(Gtk.Align.START)
        island.set_margin_top(_ISLAND_MARGIN)
        island.set_margin_end(_ISLAND_MARGIN)
        return island

    def _sync_islands(self, *_args: object) -> None:
        wide = not self.split.get_collapsed()
        self.start_island.set_visible(wide and not self.start_controls.get_empty())
        self.end_island.set_visible(wide and not self.end_controls.get_empty())

    def _build_summary(self) -> Gtk.Box:
        self._summary_vm: SidebarSummaryViewModel = SidebarSummaryViewModel(self.tabs.client)
        self.plan_page: PlanGraph | None = None
        self.review_page: ReviewPage | None = None
        self.git_pane: GitPane | None = None
        self._git_badged: set[ProjectId] = set()
        self._git_badge_timer: int | None = None
        _ = self.tabs.model.connect("items-changed", self._tab_list_changed)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.project_bar: ProjectBar = ProjectBar(self.tabs, self.tabs_sidebar)
        body.append(self.project_bar)
        self.tabs_sidebar.set_vexpand(True)
        body.append(self.tabs_sidebar)
        self.summary_card: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.summary_card.add_css_class("ac-summary-card")
        ui_state = UiState()
        self.plan_card: PlanCard = PlanCard(on_activate=self._open_plan, with_heading=False)
        self.quota_card: QuotaCard = QuotaCard(with_heading=False)
        self.git_card: GitCard = GitCard()
        self.quota_card.on_refresh = self._refresh_limits
        self._git_card_project: ProjectId | None = None
        self._code_font: Gtk.CssProvider | None = None
        self._code_font_css: str = ""
        self.plan_section: SummarySection = SummarySection(
            "plan", translate("plan.card.title"), self.plan_card, ui_state,
        )
        self.quota_section: SummarySection = SummarySection(
            "limits", self.quota_card.title(), self.quota_card, ui_state,
        )
        self.git_section: SummarySection = SummarySection("git", translate("git.card.title"), self.git_card, ui_state)
        self.summary_card.append(self.plan_section)
        self.summary_card.append(Gtk.Separator())
        self.summary_card.append(self.quota_section)
        self.summary_card.append(Gtk.Separator())
        self.summary_card.append(self.git_section)
        body.append(self.summary_card)
        return body

    def _setup_terminals(self, content: Adw.ToolbarView) -> None:
        self.context_title: Adw.WindowTitle = Adw.WindowTitle(title="AConsole")
        self.content_header.set_title_widget(self.context_title)
        self._terminals_disposed: bool = False
        self.terminal_pages: dict[str, TerminalPage] = {}
        self.terminal_stack: Gtk.Stack = Gtk.Stack()
        self.terminal_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.empty_terminal_page: Adw.StatusPage = Adw.StatusPage(title=terminal_text("terminal.empty"))
        self.empty_terminal_page.set_child(Gtk.Button(label=tabs_text("tabs.new"), action_name="win.new-tab"))
        _ = self.terminal_stack.add_named(self.empty_terminal_page, "empty")
        content.set_content(self.terminal_stack)

    def _show_selected_terminal(self, *, show_content: bool = True) -> None:
        if self._terminals_disposed:
            return
        self._sync_context()
        if show_content and self.content_view.get_content() is not self.terminal_stack:
            self.plan_page = None
            self.review_page = None
            self.git_pane = None
            self.content_view.set_content(self.terminal_stack)
        selected = self.selection.get_selected_item()
        if not isinstance(selected, TabItem):
            self.terminal_stack.set_visible_child_name("empty")
            return
        page = self._terminal_page(str(selected.tab_id))
        self.terminal_stack.set_visible_child(page)
        page.start()

    def _sync_context(self) -> None:
        snapshot = self.tabs.client.store.snapshot
        selected = self.selection.get_selected_item()
        tab = next((tab for tab in (() if snapshot is None else snapshot.tabs)
                    if isinstance(selected, TabItem) and str(tab.id) == str(selected.tab_id)), None)
        project_id = tab.project_id if tab is not None else (snapshot.active_project if snapshot is not None else None)
        project = next((project for project in (() if snapshot is None else snapshot.projects)
                        if project.id == project_id), None)
        name = project.name if project is not None else "AConsole"
        self.context_title.set_title(f"{tab.title} · {tab.agent.value}" if tab is not None else name)
        self.context_title.set_subtitle(" · ".join(part for part in (name, tab.branch if tab else "") if part))
        self.set_title(f"{tab.title} — {name} — AConsole" if tab is not None else f"{name} — AConsole")
        self.empty_terminal_page.set_title(terminal_text("terminal.empty" if tab is not None else "terminal.start"))

    def _terminal_page(self, tab_id: str) -> TerminalPage:
        page = self.terminal_pages.get(tab_id)
        if page is None:
            page = TerminalPage(self.tabs.client, TabId(tab_id), self._terminal_new, self._terminal_close)
            page.set_zoom(self._terminal_zoom)
            if self._terminal_settings is not None:
                page.apply_settings(self._terminal_settings)
            self.terminal_pages[tab_id] = page
            _ = self.terminal_stack.add_named(page, tab_id)
        return page

    def _terminal_new(self) -> None:
        _ = self.app_vm.dispatch("new-tab")

    def _select_tab(self, tab_id: TabId) -> None:
        snapshot = self.tabs.client.store.snapshot
        for tab in snapshot.tabs if snapshot is not None else ():
            if str(tab.id) == str(tab_id):
                self.project_bar.show_project(str(tab.project_id))
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
        self._connection_unsubscribe()
        self.approval_panel.stop()
        self.approval_alerts.clear()
        for page in self.terminal_pages.values():
            page.close()

    def _install_quota_banner(self, content: Adw.ToolbarView) -> None:
        self.connection_banner: Adw.Banner = Adw.Banner(title=setup_text("connection.reconnecting"))
        self.connection_banner.set_button_label(setup_text("health.title"))
        _ = self.connection_banner.connect("button-clicked", self._connection_details)
        content.add_top_bar(self.connection_banner)
        self.quota_banner: Adw.Banner = Adw.Banner(title=translate("quota.critical"))
        self.quota_banner.set_button_label(translate("quota.details"))
        _ = self.quota_banner.connect("button-clicked", self._quota_details)
        content.add_top_bar(self.quota_banner)

    def _connection_details(self, _banner: Adw.Banner) -> None:
        self._open_health()

    def _connection_changed(self, store: CoreStore) -> None:
        self.connection_banner.set_revealed(store.reconnecting)
        self.approval_panel.set_sensitive(not store.reconnecting)
        self.new_tab_button.set_sensitive(not store.reconnecting)

    def _quota_details(self, _banner: Adw.Banner) -> None:
        _ = self.quota_card.open_details(self)

    def _update_summary(self) -> None:
        plan = self._summary_vm.plan
        self.plan_card.set_plan(plan)
        if self.plan_page is not None and plan != self.plan_page.plan:
            if plan is not None and plan.id == self.plan_page.plan.id:
                if not self.plan_page.update(plan):
                    self._open_plan(plan.id)
            else:
                self._show_selected_terminal()
        self.plan_section.set_detail(f"{plan.done}/{plan.total}" if plan is not None else "")
        self.quota_card.tabs = self._summary_vm.tabs
        self.quota_card.set_accounts(self._summary_vm.accounts)
        self._sync_quota_header()
        self._follow_git_project()
        critical = any(
            self._quota_level(window.used_pct) == "critical"
            for account in self._summary_vm.accounts for window in account.quota
        )
        self.quota_banner.set_revealed(critical)
        self.end_island.set_margin_top(_ISLAND_MARGIN + (_BANNER_HEIGHT if critical else 0))

    def _open_plan(self, plan_id: PlanId) -> None:
        plan = self._summary_vm.plan
        if plan is not None and plan.id == plan_id:
            self.plan_page = PlanGraph(plan, self._close_plan, self._select_tab, self._decide_node)
            self.content_view.set_content(self.plan_page)

    def _decide_node(self, node_id: NodeId, accept: bool) -> None:  # noqa: FBT001 -- the plan page's callback
        _ = spawn_task(self._set_node_decision(node_id, accept=accept))

    async def _set_node_decision(self, node_id: NodeId, *, accept: bool) -> None:
        """Accepting ends the node. Sending back puts it in the queue again, through the one step the board allows."""
        moves = (
            ((NodeStatus.DONE, None),)
            if accept
            else ((NodeStatus.FAILED, translate("plan.sent_back_note")), (NodeStatus.PENDING, None))
        )
        try:
            for status, note in moves:
                _ = await self.tabs.client.request(
                    "plan/setNodeStatus", SetNodeStatusParams(node_id=node_id, status=status, note=note), PlanNodeView,
                )
        except Exception as error:  # noqa: BLE001 -- a refusal stays visible; the node stays as the core keeps it
            self._tab_error(translate("plan.decision_error").format(error=error))
            if self.plan_page is not None:
                self.plan_page.decision_failed()

    def _close_plan(self) -> None:
        self._show_selected_terminal()

    def _open_review(self, tab_id: str | None = None) -> None:
        """The chosen tab's changes and checkpoints. Without a tab: the selected one."""
        if tab_id is None:
            selected = self.selection.get_selected_item()
            if not isinstance(selected, TabItem):
                self._tab_error(review_text("review.no_tab"))
                return
            tab_id = str(selected.tab_id)
        else:
            self._select_tab(TabId(tab_id))
        snapshot = self.tabs.client.store.snapshot
        title = next(
            (tab.title for tab in (snapshot.tabs if snapshot is not None else ()) if str(tab.id) == tab_id), "",
        )
        self.plan_page = None
        self.git_pane = None
        self.review_page = ReviewPage(
            ReviewViewModel(self.tabs.client, TabId(tab_id)), title, self._show_selected_terminal, self._tab_error,
        )
        self.content_view.set_content(self.review_page)

    def _open_git(self) -> None:
        """The active project's graph. With a tab selected it sits beside that tab's changes and checkpoints."""
        snapshot = self.tabs.client.store.snapshot
        project = snapshot.active_project if snapshot is not None else None
        selected = self.selection.get_selected_item()
        tab = next(
            (
                item
                for item in (snapshot.tabs if snapshot is not None else ())
                if isinstance(selected, TabItem) and str(item.id) == str(selected.tab_id)
            ),
            None,
        )
        project = tab.project_id if tab is not None else project
        if project is None:
            self._tab_error(review_text("git.no_project"))
            return
        vm = GitViewModel(self.tabs.client, project)
        vm.work_session_id = None if tab is None else tab.work_session_id
        pane = GitPane(vm, self._tab_error, on_resume_tab=self._resume_work_session_tab)
        self.git_pane = pane
        self.plan_page = None
        if tab is None:
            self.review_page = None
            self.content_view.set_content(_git_alone(pane, self._show_selected_terminal))
            return
        self.review_page = ReviewPage(
            ReviewViewModel(self.tabs.client, tab.id), tab.title, self._show_selected_terminal, self._tab_error,
            git_pane=pane,
        )
        self.review_page.stack.set_visible_child_name("git")
        self.content_view.set_content(self.review_page)

    def _resume_work_session_tab(self, tab_id: TabId) -> None:
        previous = self.terminal_pages.get(str(tab_id))
        self._select_tab(tab_id)
        if previous is not None:
            previous.resume_after_handoff()

    def _request_git(self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None) -> None:
        self._open_git()

    def _request_review(self, _action: Gio.SimpleAction, parameter: GLib.Variant | None) -> None:
        if parameter is not None:
            self._open_review(parameter.get_string())

    def _request_fork(self, _action: Gio.SimpleAction, parameter: GLib.Variant | None) -> None:
        if parameter is not None:
            _ = spawn_task(self._fork_tab(TabId(parameter.get_string())))

    async def _fork_tab(self, tab_id: TabId) -> None:
        try:
            tab = await self.app_vm.client.request("tab/fork", TabIdParams(tab_id=tab_id), TabView)
        except Exception as error:  # noqa: BLE001 -- the original tab remains selected and unchanged
            self.toast_overlay.add_toast(Adw.Toast.new(str(error)))
            return
        self._select_tab(tab.id)

    def _install_tab_actions(self) -> None:
        self._closing_tab_dialogs: dict[str, Adw.AlertDialog] = {}
        self._checking_tab_close: set[str] = set()
        toggle = Gio.SimpleAction.new("toggle-sidebar", None)
        _ = toggle.connect("activate", self._toggle_sidebar)
        self.add_action(toggle)
        new_tab = Gio.SimpleAction.new("new-tab", None)
        _ = new_tab.connect("activate", self._new_tab)
        self.add_action(new_tab)
        self.close_tab_action: Gio.SimpleAction = Gio.SimpleAction.new("request-close-tab", GLib.VariantType.new("s"))
        self.close_tab_action.set_enabled(True)
        _ = self.close_tab_action.connect("activate", self._request_close_tab)
        self.add_action(self.close_tab_action)
        fork = Gio.SimpleAction.new("fork-tab", GLib.VariantType.new("s"))
        _ = fork.connect("activate", self._request_fork)
        self.add_action(fork)
        review = Gio.SimpleAction.new("open-review", GLib.VariantType.new("s"))
        _ = review.connect("activate", self._request_review)
        self.add_action(review)
        git = Gio.SimpleAction.new("open-git", None)
        _ = git.connect("activate", self._request_git)
        self.add_action(git)

    def _init_shortcuts(self, app: Adw.Application) -> None:
        self.palette_dialog: PaletteDialog | None = None
        self._terminal_zoom: float = ZOOM_DEFAULT
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
        self.health_dialog: HealthDialog | None = None
        self.setup_wizard: SetupWizard | None = None
        self._setup_shown: bool = False
        self.app_vm.action_handler = self._handle_action
        self.shortcut_manager: ShortcutManager = ShortcutManager(self, self.app_vm)
        self.app_vm.client.store.subscribe(self._store_event)
        self.app_vm.client.store.subscribe_snapshot(self._snapshot_changed)
        _ = spawn_task(self._refresh_settings())

    def _toggle_sidebar(self, _action: Gio.SimpleAction, _parameter: object) -> None:
        _ = self.app_vm.dispatch("toggle-sidebar")

    def _new_tab(self, _action: Gio.SimpleAction, _parameter: object) -> None:
        _ = self.app_vm.dispatch("new-tab")

    def _tab_opened(self, tab_id: TabId) -> None:
        self._select_tab(tab_id)
        # The tab may reach the store just after the reply.
        _ = GLib.timeout_add(_TAB_ARRIVAL_MS, self._select_tab_later, tab_id)

    def _select_tab_later(self, tab_id: TabId) -> bool:
        self._select_tab(tab_id)
        return GLib.SOURCE_REMOVE

    def _close_tab(self, tab_id: str) -> None:
        _ = spawn_task(self._close_tab_now(tab_id, None))

    async def _close_tab_now(
        self, tab_id: str, decision: Literal["keep", "remove"] | None, *, agent_confirmed: bool = False,
    ) -> None:
        if decision is None and not agent_confirmed:
            if tab_id in self._closing_tab_dialogs or tab_id in self._checking_tab_close:
                return
            self._checking_tab_close.add(tab_id)
            try:
                check = await self.tabs.client.request("app/quitCheck", EmptyParams(), QuitCheckResult)
                running = TabId(tab_id) in check.busy_tabs
            except Exception:  # noqa: BLE001 -- an unknown process must not be stopped without confirmation
                running = True
            finally:
                self._checking_tab_close.discard(tab_id)
            snapshot = self.tabs.client.store.snapshot
            pending = snapshot is not None and any(str(view.tab_id) == tab_id for view in snapshot.approvals)
            if running or pending:
                dialog = Adw.AlertDialog(heading=tabs_text("tabs.stop_heading"), body=tabs_text("tabs.stop_body"))
                dialog.add_response("cancel", tabs_text("tabs.close_cancel"))
                dialog.add_response("close", tabs_text("tabs.stop_close"))
                dialog.set_default_response("cancel")
                dialog.set_close_response("cancel")
                dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
                self._closing_tab_dialogs[tab_id] = dialog
                _ = dialog.connect("response", self._stop_tab_decided, tab_id)
                dialog.present(self)
                return
        try:
            result = await self.tabs.close(tab_id, decision)
        except Exception as error:  # noqa: BLE001 -- a refusal stays visible; the node stays as the core keeps it
            self._tab_error(tabs_text("tabs.close_error").format(error=error))
            return
        if result.needs_decision:
            dialog = Adw.AlertDialog(
                heading=tabs_text("tabs.close_heading"),
                body=tabs_text(f"tabs.reason.{result.dirty_summary}") if result.dirty_summary in {
                    "dirty", "ahead", "ignored", "kept",
                } else tabs_text("tabs.close_body"),
            )
            dialog.add_response("cancel", tabs_text("tabs.close_cancel"))
            dialog.add_response("keep", tabs_text("tabs.close_keep"))
            dialog.add_response("remove", tabs_text("tabs.close_remove"))
            dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("keep")
            dialog.set_close_response("cancel")
            _ = dialog.connect("response", self._close_decided, tab_id)
            dialog.present(self)
            return
        if result.closed and result.dirty_summary:
            reason = tabs_text(f"tabs.reason.{result.dirty_summary}") if result.dirty_summary in {
                "dirty", "ahead", "ignored", "kept",
            } else tabs_text("tabs.close_body")
            self._tab_error(tabs_text("tabs.close_kept").format(reason=reason))

    def _stop_tab_decided(self, _dialog: Adw.AlertDialog, response: str, tab_id: str) -> None:
        _ = self._closing_tab_dialogs.pop(tab_id, None)
        if response == "close":
            _ = spawn_task(self._close_tab_now(tab_id, None, agent_confirmed=True))

    def _close_decided(self, _dialog: Adw.AlertDialog, response: str, tab_id: str) -> None:
        if response == "keep":
            _ = spawn_task(self._close_tab_now(tab_id, "keep"))
        elif response == "remove":
            _ = spawn_task(self._close_tab_now(tab_id, "remove"))

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
        self._sync_approvals(self.tabs.client.store)
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

    def action_handlers(self) -> dict[str, Callable[[], bool]]:
        """Every shortcut action. Each answers whether it took the key; False leaves it to the focused widget."""
        return {
            "toggle-sidebar": self._toggle_sidebar_now,
            "palette": self._done(self._open_palette),
            "new-tab": self._done(self._present_new_tab),
            "close-tab": self._close_selected_tab,
            "next-tab": lambda: self._step_tab(1),
            "previous-tab": lambda: self._step_tab(-1),
            **{f"tab-{number}": self._tab_at(number) for number in range(1, 10)},
            "settings": self._done(self._open_preferences),
            "diff": self._done(self._open_review),
            "git": self._done(self._open_git),
            "terminal-search": self._search_terminal,
            "copy": self._copy,
            "paste": self._paste,
            "shortcuts": self._show_shortcuts,
            "zoom-in": lambda: self._zoom_by(1),
            "zoom-out": lambda: self._zoom_by(-1),
            "zoom-reset": lambda: self._zoom(ZOOM_DEFAULT),
            "quit": self._quit,
            # The shortcut manager itself lets the next key through.
            "send-next-key": lambda: True,
            **{
                action: self._attention(action)
                for action in (
                    "next-attention", "attention-next", "attention-previous", "approve", "attention-allow",
                    "deny", "attention-deny", "attention-open",
                )
            },
        }

    def _handle_action(self, action_id: str) -> bool:
        handler = self.action_handlers().get(action_id)
        return handler is not None and handler()

    @staticmethod
    def _done(action: Callable[[], None]) -> Callable[[], bool]:
        def run() -> bool:
            action()
            return True

        return run

    def _attention(self, action_id: str) -> Callable[[], bool]:
        return self._done(lambda: self._handle_attention(action_id))

    def _toggle_sidebar_now(self) -> bool:
        self.split.set_show_sidebar(not self.split.get_show_sidebar())
        return True

    def _present_new_tab(self) -> None:
        NewTabDialog(self.tabs, self._tab_opened, self._tab_error).present(self)

    def _selected_page(self) -> TerminalPage | None:
        selected = self.selection.get_selected_item()
        return self._terminal_page(str(selected.tab_id)) if isinstance(selected, TabItem) else None

    def _close_selected_tab(self) -> bool:
        selected = self.selection.get_selected_item()
        if not isinstance(selected, TabItem):
            return False
        self._terminal_close(str(selected.tab_id))
        return True

    def _step_tab(self, step: int) -> bool:
        count = self.selection.get_n_items()
        if count == 0:
            return False
        at = self.selection.get_selected()
        start = 0 if at == Gtk.INVALID_LIST_POSITION else at
        return self._show_tab_at((start + step) % count)

    def _tab_at(self, number: int) -> Callable[[], bool]:
        # With fewer tabs, Alt+digit stays what it is in the terminal.
        return lambda: number <= self.selection.get_n_items() and self._show_tab_at(number - 1)

    def _show_tab_at(self, index: int) -> bool:
        self.selection.set_selected(index)
        self._show_selected_terminal()
        self._focus_terminal()
        return True

    def _search_terminal(self) -> bool:
        page = self._selected_page()
        if page is None:
            return False
        self._show_selected_terminal()
        page.show_search()
        return True

    def _copy(self) -> bool:
        page = self._selected_page()
        if page is None or not page.has_terminal_focus():
            return False
        page.copy_selection()
        return True

    def _paste(self) -> bool:
        page = self._selected_page()
        if page is None or not page.has_terminal_focus():
            return False
        page.paste_clipboard()
        return True

    def _show_shortcuts(self) -> bool:
        ShortcutsDialog(self.app_vm.bindings).present(self)
        return True

    def _zoom_by(self, steps: int) -> bool:
        return self._zoom(zoomed(self._terminal_zoom, steps))

    def _zoom(self, scale: float) -> bool:
        self._terminal_zoom = scale
        for page in self.terminal_pages.values():
            page.set_zoom(scale)
        return True

    def _quit(self) -> bool:
        self.close()
        return True

    def _install_approvals(self, content: Adw.ToolbarView) -> None:
        self.approval_panel: ApprovalPanel = ApprovalPanel(
            ApprovalsViewModel(self.tabs.client),
            on_toast=self._tab_error,
            on_open=self._open_approval_tab,
            on_left=self._focus_terminal,
        )
        # Toasts stay above it, and the overlay does not count towards the size of what it covers.
        self.approvals_overlay: Gtk.Overlay = Gtk.Overlay(child=content)
        self.approvals_overlay.add_overlay(self.approval_panel)
        _ = self.approvals_overlay.connect("get-child-position", place_at_bottom)
        self.approval_alerts: ApprovalAlerts = ApprovalAlerts(self._post_alert, self._withdraw_alert)
        _ = self.connect("notify::is-active", self._activity_changed)
        self._connection_unsubscribe: Callable[[], None] = self.tabs.client.store.subscribe_connection(
            self._connection_changed,
        )
        self._connection_changed(self.tabs.client.store)
        self._sync_approvals(self.tabs.client.store)

    def _handle_attention(self, action_id: str) -> None:
        panel = self.approval_panel
        if action_id == "next-attention":
            if not panel.advance():
                self._next_attention_tab()
                _ = panel.step(0)
        elif action_id in {"attention-next", "attention-previous"}:
            _ = panel.step(1 if action_id == "attention-next" else -1)
        elif action_id in {"approve", "attention-allow"}:
            panel.decide_focused(ApprovalDecision.ALLOW)
        elif action_id in {"deny", "attention-deny"}:
            panel.decide_focused(ApprovalDecision.DENY)
        elif action_id == "attention-open":
            focus = self.get_focus()
            # Return on a focused button still presses that button.
            if isinstance(focus, Gtk.Button):
                _ = focus.activate()
            else:
                panel.open_focused()

    def _next_attention_tab(self) -> None:
        snapshot = self.tabs.client.store.snapshot
        waiting = [tab.id for tab in (() if snapshot is None else snapshot.tabs) if tab.attention]
        if not waiting:
            return
        selected = self.selection.get_selected_item()
        current = TabId(str(selected.tab_id)) if isinstance(selected, TabItem) else None
        at = waiting.index(current) + 1 if current is not None and current in waiting else 0
        self._select_tab(waiting[at % len(waiting)])

    def _open_approval_tab(self, tab_id: TabId) -> None:
        self._select_tab(tab_id)
        self._focus_terminal()

    def _focus_terminal(self) -> None:
        selected = self.selection.get_selected_item()
        if isinstance(selected, TabItem) and self.content_view.get_content() is self.terminal_stack:
            self._terminal_page(str(selected.tab_id)).focus_terminal()

    def _sync_approvals(self, store: CoreStore) -> None:
        snapshot = store.snapshot
        if snapshot is not None:
            selected = self.selection.get_selected_item()
            titles = {str(tab.id): tab.title for tab in snapshot.tabs}
            self.approval_panel.update(
                snapshot.approvals,
                titles,
                TabId(str(selected.tab_id)) if isinstance(selected, TabItem) else None,
            )
            if not self._snapshot_mode and self.get_mapped():
                # Before mapping there is no user-visible selection to notify about.
                self.approval_alerts.update(
                    snapshot.approvals, titles, window_active=self.is_active(),
                    notify_when="unfocused" if self._terminal_settings is None else self._terminal_settings.notify_when,
                    active_tab_id=TabId(str(selected.tab_id)) if isinstance(selected, TabItem) else None,
                )

    def _activity_changed(self, _object: object, _property: object) -> None:
        self._sync_approvals(self.tabs.client.store)

    def _post_alert(self, alert: Alert) -> None:
        app = self.get_application()
        if app is not None:
            app.send_notification(alert.id, alert_notification(alert))

    def _withdraw_alert(self, alert_id: str) -> None:
        app = self.get_application()
        if app is not None:
            app.withdraw_notification(alert_id)

    def open_approval(self, tab_id: TabId) -> None:
        """A click on the system's notification."""
        self.present()
        self._open_approval_tab(tab_id)

    def _build_settings_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        for name, label in (
            ("palette", palette_text("palette.title")),
            ("settings", setup_text("prefs.title")),
            ("setup", setup_text("setup.restart")),
            ("health", setup_text("health.title")),
        ):
            action = Gio.SimpleAction.new(name, None)
            _ = action.connect("activate", self._settings_menu_action)
            self.add_action(action)
            menu.append(label, f"win.{name}")
        return menu

    def _settings_menu_action(self, action: Gio.SimpleAction, _parameter: object) -> None:
        if action.get_name() == "setup":
            _ = spawn_task(self._restart_setup())
        elif action.get_name() == "health":
            self._open_health()
        else:
            _ = self.app_vm.dispatch(action.get_name())

    def _open_health(self) -> None:
        if self.health_dialog is None:
            self.health_dialog = HealthDialog(HealthViewModel(self.tabs.client))
        self.health_dialog.present(self)

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
        except Exception:  # noqa: BLE001 -- focus reports must not break the window on disconnect
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
        self._sync_approvals(_store)
        if isinstance(event, SettingsChanged):
            _ = spawn_task(self._refresh_settings())
        elif isinstance(event, InjectToTerminal):
            self._terminal_page(str(event.tab_id)).inject(event.text, submit=event.submit)
        elif isinstance(event, CheckpointCreated):
            page = self.review_page
            if page is not None and page.viewmodel.tab_id == event.checkpoint.tab_id:
                _ = spawn_task(page.load_checkpoints())
        elif isinstance(event, GitStateUpdated):
            pane = self.git_pane
            if pane is not None and pane.viewmodel.project_id == event.project_id:
                pane.reload()
            _ = spawn_task(self._load_git_badges(event.project_id))

    def _tab_list_changed(self, _model: Gio.ListStore, _at: int, _removed: int, _added: int) -> None:
        """A project with its first tab starts showing Git in the sidebar. No timer asks git anything after this."""
        for project_id in self._tab_projects():
            if project_id not in self._git_badged:
                self._git_badged.add(project_id)
                _ = spawn_task(self._load_git_badges(project_id))
        if self._git_badge_timer is None:
            self._git_badge_timer = GLib.timeout_add_seconds(_GIT_KEEP_S, self._keep_git_badges)

    def _tab_projects(self) -> frozenset[ProjectId]:
        snapshot = self.tabs.client.store.snapshot
        return frozenset() if snapshot is None else frozenset(tab.project_id for tab in snapshot.tabs)

    def _keep_git_badges(self) -> bool:
        for project_id in self._tab_projects():
            _ = spawn_task(self._renew_git_watch(project_id))
        return GLib.SOURCE_CONTINUE

    async def _renew_git_watch(self, project_id: ProjectId) -> None:
        with contextlib.suppress(Exception):  # a hint in the sidebar: the core's own errors show elsewhere
            await GitViewModel(self.tabs.client, project_id).watch()

    async def _load_git_badges(self, project_id: ProjectId) -> None:
        with contextlib.suppress(Exception):  # a hint in the sidebar: the core's own errors show elsewhere
            state = await GitViewModel(self.tabs.client, project_id).state()
            self.tabs.apply_git(state)
            if state.project_id == self._git_card_project:
                summary = git_summary(state)
                self.git_card.set_summary(summary)
                self.git_section.set_detail(summary.base_branch or "", "warning" if summary.conflicted else None)

    def _follow_git_project(self) -> None:
        """The Git section counts the active project. Changing project asks once; Git events keep it current."""
        snapshot = self.tabs.client.store.snapshot
        project = None if snapshot is None else snapshot.active_project
        if project == self._git_card_project:
            return
        self._git_card_project = project
        self.git_card.set_summary(None)
        self.git_section.set_detail("")
        if project is not None:
            _ = spawn_task(self._load_git_badges(project))

    def _apply_code_font(self, setting: str | None) -> None:
        """Code in the window (diffs, commands, commit messages) reads in the terminal's fonts, in the same order."""
        families = font_families(setting)
        css = f".monospace {{ font-family: {css_font_family(families)}; }}" if families else ""
        if css == self._code_font_css:
            return
        self._code_font_css = css
        display = Gdk.Display.get_default()
        if display is None:
            return
        if self._code_font is None:
            self._code_font = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(
                display, self._code_font, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
            )
        self._code_font.load_from_string(css)

    async def _refresh_limits(self) -> None:
        _ = await self.tabs.client.request("quota/refresh", EmptyParams(), EmptyResult)

    def _sync_quota_header(self) -> None:
        """Folded, the section still shows the most used window, coloured by how close it is to the limit."""
        self.quota_section.set_title(self.quota_card.title())
        used = [window.used_pct for account in self._summary_vm.accounts for window in account.quota]
        known = [value for value in used if value is not None]
        if not known:
            self.quota_section.set_detail("")
            return
        peak = max(known)
        shown = {"critical": "error", "warn": "warning"}.get(self._quota_level(peak))
        if self.quota_card.display == "remaining":
            text = translate("quota.peak_left").format(pct=round(100 - peak))
        else:
            text = translate("quota.peak").format(pct=round(peak))
        self.quota_section.set_detail(text, shown)

    def _snapshot_changed(self, _store: CoreStore) -> None:
        self._sync_terminal_pages(_store)
        self._sync_approvals(_store)
        _ = spawn_task(self._refresh_settings())

    async def _refresh_settings(self) -> None:
        try:
            settings = await self.app_vm.client.request("settings/get", EmptyParams(), Settings)
        except Exception:  # noqa: BLE001 -- focus reports must not break the window on disconnect
            _LOG.debug("Could not refresh keyboard settings", exc_info=True)
            return
        self.preferences_vm.accept(settings)

    def _quota_level(self, value: float | None) -> str:
        settings = self._terminal_settings
        return quota_level(value, warn=80 if settings is None else settings.quota_warn_pct,
            critical=95 if settings is None else settings.quota_critical_pct)

    def _apply_preferences(self, settings: Settings) -> None:
        if self._terminals_disposed or settings.rev < self._settings_revision:
            return
        self._settings_revision = settings.rev
        self._terminal_settings = settings
        self.quota_card.set_thresholds(settings.quota_warn_pct, settings.quota_critical_pct)
        self.quota_card.set_display(settings.quota_display)
        self._update_summary()
        self._sync_approvals(self.tabs.client.store)
        self._apply_code_font(settings.terminal_font)
        _ = self.app_vm.apply_overrides(settings.keymap_overrides)
        for page in self.terminal_pages.values():
            page.apply_settings(settings)
        if not self._snapshot_mode:
            schemes = {"system": Adw.ColorScheme.DEFAULT, "light": Adw.ColorScheme.FORCE_LIGHT,
                       "dark": Adw.ColorScheme.FORCE_DARK}
            Adw.StyleManager.get_default().set_color_scheme(schemes[settings.theme])


def _git_alone(pane: GitPane, on_close: Callable[[], None]) -> Gtk.Box:
    """No tab is selected: the project's graph on its own."""
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    page.add_css_class("ac-plan-page")
    heading = Gtk.Box(spacing=12)
    heading.add_css_class("ac-plan-heading")
    back = Gtk.Button(label=review_text("git.back"))
    back.add_css_class("ac-plan-back")


    def closed(_button: Gtk.Button) -> None:
        on_close()

    _ = back.connect("clicked", closed)
    heading.append(back)
    title = Gtk.Label(label=review_text("git.title"), xalign=0, hexpand=True)
    title.add_css_class("title-4")
    heading.append(title)
    page.append(heading)
    page.append(pane)
    return page
