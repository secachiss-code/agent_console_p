"""Explicit, revision-aware application preferences."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Literal

import gi
import msgspec

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from aconsole.contracts.app import ProjectApprovalPolicyParams, ProjectView  # noqa: E402
from aconsole.contracts.common import AgentKind  # noqa: E402
from aconsole.contracts.mcp_catalog import TOOL_CATALOG  # noqa: E402
from aconsole.contracts.settings import Settings, SettingsPatch  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.viewmodels.logic.approval_modes import harness_effect_key, mode_enabled  # noqa: E402
from aconsole.ui.viewmodels.logic.fonts import add_family, font_families  # noqa: E402
from aconsole.ui.viewmodels.logic.quota_fmt import agent_name  # noqa: E402
from aconsole.ui.viewmodels.setup_vm import PreferencesViewModel  # noqa: E402
from aconsole.ui.views.setup.messages import _ as translate  # noqa: E402

_THEMES: tuple[Literal["system", "light", "dark"], ...] = ("system", "light", "dark")
_QUOTA_DISPLAYS: tuple[Literal["used", "remaining"], ...] = ("used", "remaining")
_APPROVAL_MODES: tuple[Literal["inline", "native"], ...] = ("inline", "native")
_POLICY_MODES: tuple[Literal["ask", "allow", "deny", "scenario"], ...] = ("ask", "allow", "deny", "scenario")
_PROJECT_POLICIES: tuple[Literal["inherit", "ask", "allow", "deny"], ...] = ("inherit", "ask", "allow", "deny")
_CLOSE_OPENING_TICKS = 3
_HARNESSES: tuple[AgentKind, ...] = (AgentKind.CLAUDE, AgentKind.CODEX, AgentKind.OPENCODE, AgentKind.CURSOR)


def _mcp_page() -> Adw.PreferencesPage:
    page = Adw.PreferencesPage(title=translate("prefs.mcp"), icon_name="utilities-terminal-symbolic")
    for category in ("plan", "node", "step", "knowledge"):
        group = Adw.PreferencesGroup(title=translate(f"prefs.mcp.group.{category}"))
        if category == "plan":
            group.set_description(translate("prefs.mcp.optional"))
        for tool in TOOL_CATALOG:
            if tool.group != category:
                continue
            title = translate(f"prefs.mcp.tool.{tool.name}")
            mode = translate(f"prefs.mcp.mode.{tool.mode or 'both'}")
            row = Adw.ExpanderRow(title=tool.name, subtitle=title)
            row.add_row(Adw.ActionRow(title=mode, subtitle=GLib.markup_escape_text(tool.arguments)))
            example = Adw.ActionRow(
                title=translate("prefs.mcp.example"),
                subtitle=GLib.markup_escape_text(tool.example),
            )
            button = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER)
            button.set_tooltip_text(translate("prefs.mcp.copy"))
            _ = button.connect("clicked", _copy_mcp_example, tool.example)
            example.add_suffix(button)
            row.add_row(example)
            group.add(row)
        page.add(group)
    return page


def _copy_mcp_example(button: Gtk.Button, example: str) -> None:
    button.get_clipboard().set(example)


class PreferencesDialog(Adw.Dialog):
    def __init__(self, viewmodel: PreferencesViewModel, menu: Gio.MenuModel | None = None) -> None:
        super().__init__(
            title=translate("prefs.title"),
            content_width=720,
            content_height=560,
            width_request=320,
            height_request=300,
        )
        self.viewmodel: PreferencesViewModel = viewmodel
        self.worker: asyncio.Task[None] | None = None
        self.loading: bool = False
        self._saving: bool = False
        self._submitted: SettingsPatch = SettingsPatch()
        self._active: bool = False
        self._generation: int = 0
        self._shown: Settings | None = None
        self._project: ProjectView | None = None
        self._project_loaded: int = 0
        self._danger_ok: bool = False
        self._unsubscribe: Callable[[], None] | None = None
        self._close_prompt: Adw.AlertDialog | None = None
        self._close_tick_id: int = 0
        self._close_ticks: int = 0
        self._discard_on_close: bool = False
        _ = self.connect("unmap", self._cancel_pending_close)
        self.set_can_close(False)
        _ = self.connect("close-attempt", self._close_attempt)
        self._pages: Gtk.Stack = Gtk.Stack(vexpand=True)
        self._editors: tuple[Adw.PreferencesPage, ...] = self._build_categories()
        self._build_layout(menu)
        self._watch_draft()
        _ = self.connect("closed", self._closed)

    def _build_categories(self) -> tuple[Adw.PreferencesPage, ...]:
        appearance = Adw.PreferencesPage()
        terminal = Adw.PreferencesPage()
        self.limits_page: Adw.PreferencesPage = Adw.PreferencesPage()
        approvals = Adw.PreferencesPage()
        self.mcp_page: Adw.PreferencesPage = _mcp_page()
        self._build_appearance(appearance)
        self._build_terminal(terminal)
        self._build_limits(self.limits_page)
        self.limits_page.add(self._build_harness())
        approvals.add(self._build_approvals())
        for page, name in (
            (appearance, "appearance"),
            (terminal, "terminal"),
            (self.limits_page, "tools_limits"),
            (approvals, "permissions"),
            (self.mcp_page, "mcp"),
        ):
            _ = self._pages.add_titled(page, name, translate(f"prefs.{name}"))
        return appearance, terminal, self.limits_page, approvals

    def _build_layout(self, menu: Gio.MenuModel | None) -> None:
        sidebar = Gtk.StackSidebar(stack=self._pages)
        sidebar.add_css_class("ac-preferences-sidebar")
        self._pages.add_css_class("ac-preferences-content")
        self._layout: Adw.OverlaySplitView = Adw.OverlaySplitView(
            sidebar=sidebar,
            content=self._pages,
            min_sidebar_width=164,
            max_sidebar_width=205,
            sidebar_width_fraction=0.28,
        )
        self._layout.set_vexpand(True)
        _ = self._pages.connect("notify::visible-child", self._category_changed)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 560sp"))
        narrow.add_setter(self._layout, "collapsed", True)  # noqa: FBT003 -- GI uses positional GValues.
        narrow.add_setter(self._layout, "show-sidebar", False)  # noqa: FBT003 -- GI uses positional GValues.
        self.add_breakpoint(narrow)

        header = Adw.HeaderBar()
        toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", tooltip_text=translate("prefs.categories"))
        toggle.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [GObject.Value(GObject.TYPE_STRING, translate("prefs.categories"))],
        )
        _ = self._layout.bind_property("collapsed", toggle, "visible", GObject.BindingFlags.SYNC_CREATE)
        _ = self._layout.bind_property(
            "show-sidebar",
            toggle,
            "active",
            GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.BIDIRECTIONAL,
        )
        header.pack_start(toggle)
        if menu is not None:
            header.pack_end(
                Gtk.MenuButton(
                    icon_name="open-menu-symbolic", menu_model=menu, tooltip_text=translate("prefs.commands")
                )
            )
        view = Adw.ToolbarView(content=self._layout)
        view.add_top_bar(header)
        view.add_bottom_bar(self._build_actions())
        self.set_child(view)

    def _build_actions(self) -> Gtk.Box:
        actions = Gtk.Box(spacing=12)
        actions.add_css_class("ac-preferences-actions")
        self.save_button: Gtk.Button = Gtk.Button(
            label=translate("prefs.apply"),
            sensitive=False,
            valign=Gtk.Align.CENTER,
        )
        self.save_button.add_css_class("suggested-action")
        _ = self.save_button.connect("clicked", self._save_clicked)
        self.status_label: Gtk.Label = Gtk.Label(xalign=0, wrap=True, hexpand=True)
        actions.append(self.status_label)
        actions.append(self.save_button)
        return actions

    def _watch_draft(self) -> None:
        _ = self.font_entry.connect("changed", self._draft_changed)
        self._numbers: dict[str, Gtk.SpinButton] = {
            "terminal_font_size": self.font_size,
            "scrollback_lines": self.scrollback,
            "quota_warn_pct": self.quota_warn,
            "quota_critical_pct": self.quota_critical,
            "approval_hold_seconds": self.approval_hold,
        }
        for spin in self._numbers.values():
            _ = spin.connect("changed", self._draft_changed)
            _ = spin.connect("value-changed", self._draft_changed)
        for row in (self.theme, self.quota_display, self.approvals_mode, self.project_policy):
            _ = row.connect("notify::selected", self._draft_changed)
        for row in self._quota_switches.values():
            _ = row.connect("notify::active", self._draft_changed)

    def _draft_changed(self, *_args: object) -> None:
        if self.loading or self._saving or self._shown is None:
            return
        dirty = self._has_changes()
        self._sync_apply()
        key = self._validation_error() or ("prefs.unsaved" if dirty else None)
        self.status_label.set_label(translate(key) if key is not None else "")

    def _validation_error(self) -> str | None:
        if any(_spin_value(spin) is None for spin in self._numbers.values()):
            return "prefs.invalid_number"
        if _spin_or_current(self.quota_warn) > _spin_or_current(self.quota_critical):
            return "prefs.quota_threshold_order"
        return None

    def _can_apply(self) -> bool:
        return not self.loading and not self._saving and self._has_changes() and self._validation_error() is None

    def _sync_apply(self) -> None:
        enabled = self._can_apply()
        self.save_button.set_sensitive(enabled)
        if self._close_prompt is not None:
            self._close_prompt.set_response_enabled("save", enabled)

    def set_visible_page(self, page: Adw.PreferencesPage) -> None:
        self._pages.set_visible_child(page)

    def _category_changed(self, _stack: Gtk.Stack, _property: object) -> None:
        if self._layout.get_collapsed():
            self._layout.set_show_sidebar(False)

    def _build_terminal(self, page: Adw.PreferencesPage) -> None:
        terminal = Adw.PreferencesGroup(title=translate("prefs.terminal"))
        self.font_entry: Adw.EntryRow = Adw.EntryRow(title=translate("prefs.font"))
        self.font_entry.set_tooltip_text(translate("prefs.font_tip"))
        self.font_button: Gtk.Button = Gtk.Button(
            icon_name="list-add-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text=translate("prefs.font_add"),
        )
        self.font_button.add_css_class("flat")
        _ = self.font_button.connect("clicked", self._choose_font)
        self.font_entry.add_suffix(self.font_button)
        terminal.add(self.font_entry)
        self.font_size: Gtk.SpinButton = Gtk.SpinButton.new_with_range(8, 32, 1)
        self.scrollback: Gtk.SpinButton = Gtk.SpinButton.new_with_range(1000, 200000, 1000)
        self._add_spin(terminal, self.font_size, "prefs.font_size")
        self._add_spin(terminal, self.scrollback, "prefs.scrollback")
        page.add(terminal)

    def _build_appearance(self, page: Adw.PreferencesPage) -> None:
        appearance = Adw.PreferencesGroup(title=translate("prefs.appearance"))
        self.theme: Adw.ComboRow = Adw.ComboRow(
            title=translate("prefs.theme"),
            model=Gtk.StringList.new([translate(f"prefs.{theme}") for theme in _THEMES]),
        )
        appearance.add(self.theme)
        page.add(appearance)

    def _build_limits(self, page: Adw.PreferencesPage) -> None:
        limits = Adw.PreferencesGroup(title=translate("quota.title"))
        self.quota_display: Adw.ComboRow = Adw.ComboRow(
            title=translate("prefs.quota_display"),
            model=Gtk.StringList.new([translate(f"prefs.quota_{display}") for display in _QUOTA_DISPLAYS]),
        )
        limits.add(self.quota_display)
        self.quota_warn: Gtk.SpinButton = Gtk.SpinButton.new_with_range(0, 100, 1)
        self.quota_critical: Gtk.SpinButton = Gtk.SpinButton.new_with_range(0, 100, 1)
        self._add_spin(limits, self.quota_warn, "prefs.quota_warn")
        self._add_spin(limits, self.quota_critical, "prefs.quota_critical")
        page.add(limits)
        visibility = Adw.PreferencesGroup(title=translate("prefs.quota_visible_agents"))
        self._quota_switches: dict[AgentKind, Adw.SwitchRow] = {}
        for kind in AgentKind:
            toggle = Adw.SwitchRow(title=agent_name(kind.value), active=True)
            visibility.add(toggle)
            self._quota_switches[kind] = toggle
        page.add(visibility)

    def _build_approvals(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=translate("prefs.approvals"))
        self.approvals_mode: Adw.ComboRow = Adw.ComboRow(
            title=translate("prefs.approvals_mode"),
            model=Gtk.StringList.new([translate(f"prefs.approvals_{mode}") for mode in _APPROVAL_MODES]),
        )
        group.add(self.approvals_mode)
        self.approval_hold: Gtk.SpinButton = Gtk.SpinButton.new_with_range(30, 540, 30)
        self._add_spin(group, self.approval_hold, "prefs.approval_hold")
        self.policy: Adw.ComboRow = Adw.ComboRow(
            title=translate("prefs.policy"),
            model=Gtk.StringList.new([translate(f"prefs.policy_{mode}") for mode in _POLICY_MODES]),
        )
        _ = self.policy.connect("notify::selected", self._policy_selected)
        group.add(self.policy)
        self.project_policy: Adw.ComboRow = Adw.ComboRow(
            title=translate("prefs.policy_project"),
            model=Gtk.StringList.new(
                [
                    translate("prefs.policy_inherit").format(mode=translate("prefs.policy_ask")),
                    *(translate(f"prefs.policy_{mode}") for mode in _PROJECT_POLICIES[1:]),
                ]
            ),
        )
        group.add(self.project_policy)
        dangerous = Adw.ActionRow(title=translate("prefs.dangerous"))
        self.dangerous: Gtk.Switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        _ = self.dangerous.connect("notify::active", self._dangerous_active)
        dangerous.add_suffix(self.dangerous)
        dangerous.set_activatable_widget(self.dangerous)
        group.add(dangerous)
        return group

    def _build_harness(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=translate("prefs.harness"))
        self.harness_rows: dict[AgentKind, Adw.ActionRow] = {}
        for kind in _HARNESSES:
            row = Adw.ActionRow(title=kind.value)
            group.add(row)
            self.harness_rows[kind] = row
        return group

    def _choose_font(self, _button: Gtk.Button) -> None:
        """Only monospace families are offered; the one chosen joins the end of the list."""
        dialog = Gtk.FontDialog(title=translate("prefs.font_add"), modal=True)
        only_mono = Gtk.CustomFilter.new(_is_monospace)
        dialog.set_filter(only_mono)
        root = self.get_root()
        parent = root if isinstance(root, Gtk.Window) else None
        dialog.choose_family(parent, None, None, self._font_chosen)

    def _font_chosen(self, dialog: Gtk.FontDialog, result: Gio.AsyncResult) -> None:
        try:
            family = dialog.choose_family_finish(result)
        except GLib.Error:
            return  # cancelled
        self.font_entry.set_text(add_family(self.font_entry.get_text(), family.get_name()))

    @staticmethod
    def _add_spin(group: Adw.PreferencesGroup, spin: Gtk.SpinButton, key: str) -> None:
        row = Adw.ActionRow(title=translate(key))
        spin.set_valign(Gtk.Align.CENTER)
        row.add_suffix(spin)
        row.set_activatable_widget(spin)
        group.add(row)

    def open(self, parent: Gtk.Widget) -> None:
        if self._active:
            self.present(parent)
            return
        self._active = True
        self._generation += 1
        self._shown = None
        self._project = None
        self.status_label.set_label("")
        if self._unsubscribe is None:
            self._unsubscribe = self.viewmodel.subscribe(self._show_settings)
        self.save_button.set_sensitive(False)
        self.loading = True
        for page in self._editors:
            page.set_sensitive(False)
        self.present(parent)
        self.worker = spawn_task(self._load(self._generation))

    async def _load(self, generation: int) -> None:
        try:
            _ = await self.viewmodel.load()
        except Exception:  # noqa: BLE001 -- keep the dialog open for an explicit retry
            if self._active and generation == self._generation:
                self.status_label.set_label(translate("prefs.load_error"))
        finally:
            if self._active and generation == self._generation:
                self.loading = False
                for page in self._editors:
                    page.set_sensitive(self._shown is not None)
                self._sync_apply()

    def _show_settings(self, settings: Settings) -> None:
        if not self._active or settings == self._shown:
            return
        previous = self._shown
        local = self._changes(previous) if previous is not None else SettingsPatch()
        keep: set[str] = set()
        # A user may change a submitted field back to its old value before the reply arrives.
        for patch in (local, self._submitted):
            fields: dict[str, object] = msgspec.structs.asdict(patch)
            keep.update(key for key, value in fields.items() if value is not msgspec.UNSET)
        if previous is not None:
            keep.update(key for key, spin in self._numbers.items() if _spin_value(spin) is None)
        loading = self.loading
        self.loading = True
        self._show_values(settings, keep)
        self._show_policy(settings, keep)
        self._shown = settings
        self.loading = loading
        if not self._saving:
            if self._has_changes():
                key = self._validation_error() or "prefs.updated_draft"
                self.status_label.set_label(translate(key))
            elif local != SettingsPatch():
                self.status_label.set_label("")
        self._sync_apply()

    def _show_values(self, settings: Settings, keep: set[str]) -> None:
        if "terminal_font" not in keep:
            self.font_entry.set_text(settings.terminal_font or "")
        for key, value in (
            ("terminal_font_size", settings.terminal_font_size),
            ("scrollback_lines", settings.scrollback_lines),
            ("approval_hold_seconds", settings.approval_hold_seconds),
            ("quota_warn_pct", settings.quota_warn_pct),
            ("quota_critical_pct", settings.quota_critical_pct),
        ):
            if key not in keep:
                spin = self._numbers[key]
                spin.set_value(value)
                spin.set_text(f"{value:.0f}")
        if "theme" not in keep:
            self.theme.set_selected(_THEMES.index(settings.theme))
        if "quota_display" not in keep:
            self.quota_display.set_selected(_QUOTA_DISPLAYS.index(settings.quota_display))
        if "approvals_mode" not in keep:
            self.approvals_mode.set_selected(_APPROVAL_MODES.index(settings.approvals_mode))
        if "quota_hidden_agents" not in keep:
            for kind, row in self._quota_switches.items():
                row.set_active(kind not in settings.quota_hidden_agents)

    def save(self, *, close_after: bool = False) -> None:
        if self._shown is None or not self._can_apply():
            return
        patch = self._changes(self._shown)
        project = self._project_params()
        self._saving = True
        self._submitted = patch
        self.status_label.set_label(translate("prefs.saving"))
        self._sync_apply()
        self.worker = spawn_task(self._save(patch, project, self._generation, close_after=close_after))

    def _changes(self, shown: Settings) -> SettingsPatch:
        font = ", ".join(font_families(self.font_entry.get_text())) or None
        size = int(_spin_or_current(self.font_size))
        scrollback = int(_spin_or_current(self.scrollback))
        theme = _THEMES[min(self.theme.get_selected(), len(_THEMES) - 1)]
        display = _QUOTA_DISPLAYS[min(self.quota_display.get_selected(), len(_QUOTA_DISPLAYS) - 1)]
        hidden = frozenset(kind for kind, row in self._quota_switches.items() if not row.get_active())
        mode = _APPROVAL_MODES[min(self.approvals_mode.get_selected(), len(_APPROVAL_MODES) - 1)]
        hold = int(_spin_or_current(self.approval_hold))
        warn = _spin_or_current(self.quota_warn)
        critical = _spin_or_current(self.quota_critical)
        policy = self._policy_value()
        dangerous: Literal["ask", "allow"] = "allow" if self.dangerous.get_active() else "ask"
        return SettingsPatch(
            approvals_mode=mode if mode != shown.approvals_mode else msgspec.UNSET,
            approval_policy=policy if policy != shown.approval_policy else msgspec.UNSET,
            approval_dangerous=dangerous if dangerous != shown.approval_dangerous else msgspec.UNSET,
            approval_hold_seconds=hold if hold != shown.approval_hold_seconds else msgspec.UNSET,
            quota_display=display if display != shown.quota_display else msgspec.UNSET,
            quota_hidden_agents=hidden if hidden != shown.quota_hidden_agents else msgspec.UNSET,
            terminal_font=font if font != shown.terminal_font else msgspec.UNSET,
            terminal_font_size=size if size != shown.terminal_font_size else msgspec.UNSET,
            scrollback_lines=scrollback if scrollback != shown.scrollback_lines else msgspec.UNSET,
            theme=theme if theme != shown.theme else msgspec.UNSET,
            quota_warn_pct=warn if warn != shown.quota_warn_pct else msgspec.UNSET,
            quota_critical_pct=critical if critical != shown.quota_critical_pct else msgspec.UNSET,
        )

    async def _save(
        self,
        patch: SettingsPatch,
        project: ProjectApprovalPolicyParams | None,
        generation: int,
        *,
        close_after: bool,
    ) -> None:
        settings_saved = False
        try:
            if patch != SettingsPatch():
                _ = await self.viewmodel.update(patch)
                settings_saved = True
            if project is not None:
                confirmed = await self.viewmodel.set_project_policy(project)
                if self._active and generation == self._generation:
                    self._project = confirmed
                    stored = confirmed.approval_policy
                    self._project_loaded = _PROJECT_POLICIES.index(stored) if stored in _PROJECT_POLICIES else 0
            if self._active and generation == self._generation:
                dirty = self._has_changes()
                key = self._validation_error() or ("prefs.unsaved" if dirty else "prefs.saved")
                self.status_label.set_label(translate(key))
                if close_after and not dirty:
                    self._request_close()
        except Exception:  # noqa: BLE001 -- never publish an unconfirmed settings patch
            if self._active and generation == self._generation:
                self.status_label.set_label(translate("prefs.partial" if settings_saved else "prefs.error"))
        finally:
            self._saving = False
            self._submitted = SettingsPatch()
            if self._active and generation == self._generation:
                self._sync_apply()

    def _show_policy(self, settings: Settings, keep: set[str]) -> None:
        if "approval_policy" not in keep:
            self.policy.set_selected(self._selected_policy(settings.approval_policy))
        if "approval_dangerous" not in keep:
            self._danger_ok = settings.approval_dangerous == "allow"
            self.dangerous.set_active(settings.approval_dangerous == "allow")
        self._show_project(settings)
        self._show_harness()
        self.dangerous.set_sensitive(self._policy_value() == "allow")

    def _policy_value(self) -> Literal["ask", "allow", "deny", "scenario"]:
        return _POLICY_MODES[min(self.policy.get_selected(), len(_POLICY_MODES) - 1)]

    @staticmethod
    def _selected_policy(mode: str) -> int:
        return _POLICY_MODES.index(mode) if mode in _POLICY_MODES else 0

    def _policy_selected(self, row: Adw.ComboRow, _pspec: GObject.ParamSpec) -> None:
        if self.loading:
            return
        mode = _POLICY_MODES[min(row.get_selected(), len(_POLICY_MODES) - 1)]
        available = mode_enabled(mode)
        if not available:
            self.status_label.set_label(translate("prefs.policy_scenario_unavailable"))
            fallback = self._selected_policy(self._shown.approval_policy) if self._shown is not None else 0
            if not mode_enabled(_POLICY_MODES[fallback]):
                fallback = 0
            self.loading = True
            row.set_selected(fallback)
            self.loading = False
            mode = _POLICY_MODES[fallback]
        self.dangerous.set_sensitive(mode == "allow")
        if available:
            self._draft_changed()

    def _dangerous_active(self, switch: Gtk.Switch, _pspec: GObject.ParamSpec) -> None:
        if self.loading:
            return
        if not switch.get_active():
            self._danger_ok = False
            self._draft_changed()
            return
        if self._danger_ok:
            self._draft_changed()
            return
        self.loading = True
        switch.set_active(False)
        self.loading = False
        dialog = Adw.AlertDialog(
            heading=translate("prefs.dangerous_confirm_title"),
            body=translate("prefs.dangerous_confirm_body"),
        )
        dialog.add_response("cancel", translate("prefs.dangerous_cancel"))
        dialog.add_response("allow", translate("prefs.dangerous_allow"))
        dialog.set_response_appearance("allow", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        _ = dialog.connect("response", self._dangerous_answered)
        dialog.present(self)

    def _dangerous_answered(self, _dialog: Adw.AlertDialog, response: str) -> None:
        if response != "allow":
            return
        self._danger_ok = True
        self.loading = True
        self.dangerous.set_active(True)
        self.loading = False
        self._draft_changed()

    def _show_project(self, settings: Settings) -> None:
        project = self.viewmodel.active_project()
        previous = self._project
        selected = self.project_policy.get_selected()
        same = previous is not None and project is not None and previous.id == project.id
        dirty = same and selected != self._project_loaded
        if same:
            project = previous
        self._project = project
        self.project_policy.set_visible(project is not None)
        if project is None:
            self._project_loaded = 0
            return
        inherited = translate("prefs.policy_inherit").format(
            mode=translate(f"prefs.policy_{settings.approval_policy}"),
        )
        self.project_policy.set_title(project.name)
        self.project_policy.set_model(
            Gtk.StringList.new(
                [
                    inherited,
                    *(translate(f"prefs.policy_{mode}") for mode in _PROJECT_POLICIES[1:]),
                ]
            )
        )
        stored = project.approval_policy
        if stored == "scenario":
            self.project_policy.set_subtitle(translate("prefs.policy_scenario_unavailable"))
        else:
            self.project_policy.set_subtitle("")
        index = 0 if stored is None or stored not in _PROJECT_POLICIES else _PROJECT_POLICIES.index(stored)
        self.project_policy.set_selected(selected if dirty else index)
        self._project_loaded = index

    def _project_params(self) -> ProjectApprovalPolicyParams | None:
        project = self._project
        if project is None or self.project_policy.get_selected() == self._project_loaded:
            return None
        chosen = _PROJECT_POLICIES[min(self.project_policy.get_selected(), len(_PROJECT_POLICIES) - 1)]
        if chosen == "inherit":
            return ProjectApprovalPolicyParams(project_id=project.id, inherit=True)
        dangerous: Literal["ask", "allow"] = "allow" if chosen == "allow" and self.dangerous.get_active() else "ask"
        return ProjectApprovalPolicyParams(
            project_id=project.id,
            approval_policy=chosen,
            approval_dangerous=dangerous,
        )

    def _show_harness(self) -> None:
        found = {agent.kind: agent for agent in self.viewmodel.agents()}
        for kind, row in self.harness_rows.items():
            agent = found.get(kind)
            title = agent.display_name if agent is not None else kind.value
            row.set_title(title)
            capabilities = agent.capabilities if agent is not None else frozenset[str]()
            row.set_subtitle(translate(harness_effect_key(capabilities)))

    def _save_clicked(self, _button: Gtk.Button) -> None:
        self.save()

    def _has_changes(self) -> bool:
        return self._shown is not None and (
            self._changes(self._shown) != SettingsPatch()
            or self._project_params() is not None
            or any(_spin_value(spin) is None for spin in self._numbers.values())
        )

    def protect_close(self) -> bool:
        """Route application exit through the draft decision before the window can disappear."""
        dirty = self._has_changes()
        if self._active and (dirty or self._saving):
            _ = self.close()
            return True
        return False

    def _close_attempt(self, _dialog: Adw.Dialog) -> None:
        if self._saving:
            self.status_label.set_label(translate("prefs.saving"))
            return
        if not self._has_changes():
            self._request_close()
            return
        if self._close_prompt is not None:
            return
        prompt = Adw.AlertDialog(heading=translate("prefs.unsaved"), body=translate("prefs.close_body"))
        self._close_prompt = prompt
        for response, key in (("cancel", "prefs.keep_editing"), ("discard", "prefs.discard"), ("save", "prefs.apply")):
            prompt.add_response(response, translate(key))
        prompt.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        prompt.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        prompt.set_response_enabled("save", self._can_apply())
        prompt.set_default_response("cancel")
        prompt.set_close_response("cancel")
        _ = prompt.connect("response", self._close_response)
        prompt.present(self)

    def _close_response(self, prompt: Adw.AlertDialog, response: str) -> None:
        if self._close_prompt is not prompt:
            return
        self._close_prompt = None
        if response == "discard":
            self._request_close(discard=True)
        elif response == "save":
            self.save(close_after=True)

    def _request_close(self, *, discard: bool = False) -> None:
        if not self._active or self._close_tick_id:
            return
        self._close_ticks = 0
        self._discard_on_close = discard
        self._close_tick_id = self.add_tick_callback(self._close_after_opening)

    def _close_after_opening(self, _widget: Gtk.Widget, _clock: Gdk.FrameClock) -> bool:
        # libadwaita 1.5 opens embedded sheets on their second map tick. Closing
        # earlier is a no-op, then that opening tick resurrects the dialog.
        # Three ticks also leave the nested alert's response stack before closing.
        self._close_ticks += 1
        if self._close_ticks < _CLOSE_OPENING_TICKS:
            return GLib.SOURCE_CONTINUE
        self._close_tick_id = 0
        if self._saving:
            return GLib.SOURCE_REMOVE
        if not self._discard_on_close and self._has_changes():
            self.status_label.set_label(translate("prefs.unsaved"))
            return GLib.SOURCE_REMOVE
        self.force_close()
        return GLib.SOURCE_REMOVE

    def _cancel_pending_close(self, _widget: Gtk.Widget) -> None:
        if self._close_tick_id:
            self.remove_tick_callback(self._close_tick_id)
            self._close_tick_id = 0

    def _closed(self, _dialog: Adw.Dialog) -> None:
        self._cancel_pending_close(self)
        self._active = False
        self._generation += 1
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None


def _is_monospace(item: object) -> bool:
    return isinstance(item, Pango.FontFamily) and item.is_monospace()


def _spin_value(spin: Gtk.SpinButton) -> float | None:
    text = spin.get_text().strip()
    current = spin.get_value()
    if text == f"{current:.0f}":
        # Keep an exact value received from the core while the control displays rounded digits.
        return current
    if not text.isdecimal():
        return None
    adjustment = spin.get_adjustment()
    try:
        value = int(text)
    except ValueError:
        return None
    return float(value) if adjustment.get_lower() <= value <= adjustment.get_upper() else None


def _spin_or_current(spin: Gtk.SpinButton) -> float:
    value = _spin_value(spin)
    return spin.get_value() if value is None else value
