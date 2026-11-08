"""Explicit, revision-aware application preferences."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Literal

import gi
import msgspec

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from aconsole.contracts.app import ProjectApprovalPolicyParams, ProjectView  # noqa: E402
from aconsole.contracts.common import AgentKind  # noqa: E402
from aconsole.contracts.settings import Settings, SettingsPatch  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.viewmodels.logic.approval_modes import harness_effect_key, mode_enabled  # noqa: E402
from aconsole.ui.viewmodels.logic.fonts import add_family, font_families  # noqa: E402
from aconsole.ui.viewmodels.setup_vm import PreferencesViewModel  # noqa: E402
from aconsole.ui.views.setup.messages import _ as translate  # noqa: E402

_THEMES: tuple[Literal["system", "light", "dark"], ...] = ("system", "light", "dark")
_QUOTA_DISPLAYS: tuple[Literal["used", "remaining"], ...] = ("used", "remaining")
_APPROVAL_MODES: tuple[Literal["inline", "native"], ...] = ("inline", "native")
_POLICY_MODES: tuple[Literal["ask", "allow", "deny", "scenario"], ...] = ("ask", "allow", "deny", "scenario")
_PROJECT_POLICIES: tuple[Literal["inherit", "ask", "allow", "deny"], ...] = ("inherit", "ask", "allow", "deny")
_HARNESSES: tuple[AgentKind, ...] = (AgentKind.CLAUDE, AgentKind.CODEX, AgentKind.OPENCODE, AgentKind.CURSOR)


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, viewmodel: PreferencesViewModel) -> None:
        super().__init__(title=translate("prefs.title"), content_width=560, content_height=560)
        self.viewmodel: PreferencesViewModel = viewmodel
        self.worker: asyncio.Task[None] | None = None
        self.loading: bool = False
        self._active: bool = False
        self._generation: int = 0
        self._shown: Settings | None = None
        self._project: ProjectView | None = None
        self._project_loaded: int = 0
        self._danger_ok: bool = False
        self._unsubscribe: Callable[[], None] | None = None
        page = Adw.PreferencesPage()
        terminal = Adw.PreferencesGroup(title=translate("prefs.terminal"))
        self.font_entry: Adw.EntryRow = Adw.EntryRow(title=translate("prefs.font"))
        self.font_entry.set_tooltip_text(translate("prefs.font_tip"))
        self.font_button: Gtk.Button = Gtk.Button(
            icon_name="list-add-symbolic", valign=Gtk.Align.CENTER, tooltip_text=translate("prefs.font_add"),
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
        appearance = Adw.PreferencesGroup(title=translate("prefs.appearance"))
        self.theme: Adw.ComboRow = Adw.ComboRow(
            title=translate("prefs.theme"),
            model=Gtk.StringList.new([translate(f"prefs.{theme}") for theme in _THEMES]),
        )
        appearance.add(self.theme)
        self.quota_display: Adw.ComboRow = Adw.ComboRow(
            title=translate("prefs.quota_display"),
            model=Gtk.StringList.new([translate(f"prefs.quota_{display}") for display in _QUOTA_DISPLAYS]),
        )
        appearance.add(self.quota_display)
        page.add(appearance)
        page.add(self._build_approvals())
        page.add(self._build_harness())
        actions = Adw.PreferencesGroup()
        self.save_button: Gtk.Button = Gtk.Button(label=translate("prefs.apply"))
        self.save_button.add_css_class("suggested-action")
        _ = self.save_button.connect("clicked", self._save_clicked)
        actions.add(self.save_button)
        self.status_label: Gtk.Label = Gtk.Label(xalign=0, wrap=True)
        actions.add(self.status_label)
        page.add(actions)
        self.add(page)
        _ = self.connect("closed", self._closed)

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
            model=Gtk.StringList.new([
                translate("prefs.policy_inherit").format(mode=translate("prefs.policy_ask")),
                *(translate(f"prefs.policy_{mode}") for mode in _PROJECT_POLICIES[1:]),
            ]),
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
        if self._unsubscribe is None:
            self._unsubscribe = self.viewmodel.subscribe(self._show_settings)
        self.save_button.set_sensitive(False)
        self.loading = True
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
                self.save_button.set_sensitive(self._shown is not None)

    def _show_settings(self, settings: Settings) -> None:
        if not self._active or settings == self._shown:
            return
        previous = self._shown
        dirty = previous is not None and self._changes(previous) != SettingsPatch()
        if previous is None or self.font_entry.get_text() == (previous.terminal_font or ""):
            self.font_entry.set_text(settings.terminal_font or "")
        if previous is None or self.font_size.get_value_as_int() == previous.terminal_font_size:
            self.font_size.set_value(settings.terminal_font_size)
        if previous is None or self.scrollback.get_value_as_int() == previous.scrollback_lines:
            self.scrollback.set_value(settings.scrollback_lines)
        if previous is None or self.theme.get_selected() == _THEMES.index(previous.theme):
            self.theme.set_selected(_THEMES.index(settings.theme))
        if previous is None or self.quota_display.get_selected() == _QUOTA_DISPLAYS.index(previous.quota_display):
            self.quota_display.set_selected(_QUOTA_DISPLAYS.index(settings.quota_display))
        if previous is None or self.approvals_mode.get_selected() == _APPROVAL_MODES.index(previous.approvals_mode):
            self.approvals_mode.set_selected(_APPROVAL_MODES.index(settings.approvals_mode))
        if previous is None or self.approval_hold.get_value_as_int() == previous.approval_hold_seconds:
            self.approval_hold.set_value(settings.approval_hold_seconds)
        self._show_policy(settings, previous)
        self._shown = settings
        if dirty:
            self.status_label.set_label(translate("prefs.updated_draft"))

    def save(self) -> None:
        if self.loading or self._shown is None or (self.worker is not None and not self.worker.done()):
            return
        patch = self._changes(self._shown)
        project = self._project_params()
        self.save_button.set_sensitive(False)
        self.worker = spawn_task(self._save(patch, project, self._generation))

    def _changes(self, shown: Settings) -> SettingsPatch:
        font = ", ".join(font_families(self.font_entry.get_text())) or None
        size = self.font_size.get_value_as_int()
        scrollback = self.scrollback.get_value_as_int()
        theme = _THEMES[min(self.theme.get_selected(), len(_THEMES) - 1)]
        display = _QUOTA_DISPLAYS[min(self.quota_display.get_selected(), len(_QUOTA_DISPLAYS) - 1)]
        mode = _APPROVAL_MODES[min(self.approvals_mode.get_selected(), len(_APPROVAL_MODES) - 1)]
        hold = self.approval_hold.get_value_as_int()
        policy = self._policy_value()
        dangerous: Literal["ask", "allow"] = "allow" if self.dangerous.get_active() else "ask"
        return SettingsPatch(
            approvals_mode=mode if mode != shown.approvals_mode else msgspec.UNSET,
            approval_policy=policy if policy != shown.approval_policy else msgspec.UNSET,
            approval_dangerous=dangerous if dangerous != shown.approval_dangerous else msgspec.UNSET,
            approval_hold_seconds=hold if hold != shown.approval_hold_seconds else msgspec.UNSET,
            quota_display=display if display != shown.quota_display else msgspec.UNSET,
            terminal_font=font if font != shown.terminal_font else msgspec.UNSET,
            terminal_font_size=size if size != shown.terminal_font_size else msgspec.UNSET,
            scrollback_lines=scrollback if scrollback != shown.scrollback_lines else msgspec.UNSET,
            theme=theme if theme != shown.theme else msgspec.UNSET,
        )

    async def _save(
        self, patch: SettingsPatch, project: ProjectApprovalPolicyParams | None, generation: int,
    ) -> None:
        try:
            if patch != SettingsPatch():
                _ = await self.viewmodel.update(patch)
            if project is not None:
                _ = await self.viewmodel.set_project_policy(project)
            if self._active and generation == self._generation:
                self.status_label.set_label(translate("prefs.saved"))
        except Exception:  # noqa: BLE001 -- never publish an unconfirmed settings patch
            if self._active and generation == self._generation:
                self.status_label.set_label(translate("prefs.error"))
        finally:
            if self._active and generation == self._generation:
                self.save_button.set_sensitive(True)

    def _show_policy(self, settings: Settings, previous: Settings | None) -> None:
        self.loading = True
        if previous is None or self._selected_policy(previous.approval_policy) == self.policy.get_selected():
            self.policy.set_selected(self._selected_policy(settings.approval_policy))
        self._danger_ok = settings.approval_dangerous == "allow"
        if previous is None or self.dangerous.get_active() == (previous.approval_dangerous == "allow"):
            self.dangerous.set_active(settings.approval_dangerous == "allow")
        self._show_project(settings)
        self._show_harness()
        self.dangerous.set_sensitive(self._policy_value() == "allow")
        self.loading = False

    def _policy_value(self) -> Literal["ask", "allow", "deny", "scenario"]:
        return _POLICY_MODES[min(self.policy.get_selected(), len(_POLICY_MODES) - 1)]

    @staticmethod
    def _selected_policy(mode: str) -> int:
        return _POLICY_MODES.index(mode) if mode in _POLICY_MODES else 0

    def _policy_selected(self, row: Adw.ComboRow, _pspec: GLib.ParamSpec) -> None:
        if self.loading:
            return
        mode = _POLICY_MODES[min(row.get_selected(), len(_POLICY_MODES) - 1)]
        if not mode_enabled(mode):
            self.status_label.set_label(translate("prefs.policy_scenario_unavailable"))
            fallback = self._selected_policy(self._shown.approval_policy) if self._shown is not None else 0
            if not mode_enabled(_POLICY_MODES[fallback]):
                fallback = 0
            self.loading = True
            row.set_selected(fallback)
            self.loading = False
            mode = _POLICY_MODES[fallback]
        self.dangerous.set_sensitive(mode == "allow")

    def _dangerous_active(self, switch: Gtk.Switch, _pspec: GLib.ParamSpec) -> None:
        if self.loading or not switch.get_active() or self._danger_ok:
            if not switch.get_active():
                self._danger_ok = False
            return
        self.loading = True
        switch.set_active(False)
        self.loading = False
        dialog = Adw.AlertDialog(
            heading=translate("prefs.dangerous_confirm_title"), body=translate("prefs.dangerous_confirm_body"),
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

    def _show_project(self, settings: Settings) -> None:
        project = self.viewmodel.active_project()
        self._project = project
        self.project_policy.set_visible(project is not None)
        if project is None:
            self._project_loaded = 0
            return
        inherited = translate("prefs.policy_inherit").format(
            mode=translate(f"prefs.policy_{settings.approval_policy}"),
        )
        self.project_policy.set_title(project.name)
        self.project_policy.set_model(Gtk.StringList.new([
            inherited, *(translate(f"prefs.policy_{mode}") for mode in _PROJECT_POLICIES[1:]),
        ]))
        stored = project.approval_policy
        if stored == "scenario":
            self.project_policy.set_subtitle(translate("prefs.policy_scenario_unavailable"))
        else:
            self.project_policy.set_subtitle("")
        index = 0 if stored is None or stored not in _PROJECT_POLICIES else _PROJECT_POLICIES.index(stored)
        self.project_policy.set_selected(index)
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
            project_id=project.id, approval_policy=chosen, approval_dangerous=dangerous,
        )

    def _show_harness(self) -> None:
        found = {agent.kind: agent for agent in self.viewmodel.agents()}
        for kind, row in self.harness_rows.items():
            agent = found.get(kind)
            title = agent.display_name if agent is not None else kind.value
            row.set_title(title)
            capabilities = agent.capabilities if agent is not None else frozenset()
            row.set_subtitle(translate(harness_effect_key(capabilities)))

    def _save_clicked(self, _button: Gtk.Button) -> None:
        self.save()

    def _closed(self, _dialog: Adw.Dialog) -> None:
        self._active = False
        self._generation += 1
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None


def _is_monospace(item: object) -> bool:
    return isinstance(item, Pango.FontFamily) and item.is_monospace()
