"""Explicit, revision-aware application preferences."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Literal

import gi
import msgspec

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from aconsole.contracts.settings import Settings, SettingsPatch  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.viewmodels.setup_vm import PreferencesViewModel  # noqa: E402
from aconsole.ui.views.setup.messages import _ as translate  # noqa: E402

_THEMES: tuple[Literal["system", "light", "dark"], ...] = ("system", "light", "dark")
_QUOTA_DISPLAYS: tuple[Literal["used", "remaining"], ...] = ("used", "remaining")
_APPROVAL_MODES: tuple[Literal["inline", "native"], ...] = ("inline", "native")


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, viewmodel: PreferencesViewModel) -> None:
        super().__init__(title=translate("prefs.title"), content_width=560, content_height=560)
        self.viewmodel: PreferencesViewModel = viewmodel
        self.worker: asyncio.Task[None] | None = None
        self.loading: bool = False
        self._active: bool = False
        self._generation: int = 0
        self._shown: Settings | None = None
        self._unsubscribe: Callable[[], None] | None = None
        page = Adw.PreferencesPage()
        terminal = Adw.PreferencesGroup(title=translate("prefs.terminal"))
        self.font_entry: Adw.EntryRow = Adw.EntryRow(title=translate("prefs.font"))
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
        return group

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
        self._shown = settings
        self.font_entry.set_text(settings.terminal_font or "")
        self.font_size.set_value(settings.terminal_font_size)
        self.scrollback.set_value(settings.scrollback_lines)
        self.theme.set_selected(_THEMES.index(settings.theme))
        self.quota_display.set_selected(_QUOTA_DISPLAYS.index(settings.quota_display))
        self.approvals_mode.set_selected(_APPROVAL_MODES.index(settings.approvals_mode))
        self.approval_hold.set_value(settings.approval_hold_seconds)

    def save(self) -> None:
        if self.loading or self._shown is None or (self.worker is not None and not self.worker.done()):
            return
        shown = self._shown
        font = self.font_entry.get_text().strip() or None
        size = self.font_size.get_value_as_int()
        scrollback = self.scrollback.get_value_as_int()
        theme = _THEMES[min(self.theme.get_selected(), len(_THEMES) - 1)]
        display = _QUOTA_DISPLAYS[min(self.quota_display.get_selected(), len(_QUOTA_DISPLAYS) - 1)]
        mode = _APPROVAL_MODES[min(self.approvals_mode.get_selected(), len(_APPROVAL_MODES) - 1)]
        hold = self.approval_hold.get_value_as_int()
        patch = SettingsPatch(
            approvals_mode=mode if mode != shown.approvals_mode else msgspec.UNSET,
            approval_hold_seconds=hold if hold != shown.approval_hold_seconds else msgspec.UNSET,
            quota_display=display if display != shown.quota_display else msgspec.UNSET,
            terminal_font=font if font != shown.terminal_font else msgspec.UNSET,
            terminal_font_size=size if size != shown.terminal_font_size else msgspec.UNSET,
            scrollback_lines=scrollback if scrollback != shown.scrollback_lines else msgspec.UNSET,
            theme=theme if theme != shown.theme else msgspec.UNSET,
        )
        self.save_button.set_sensitive(False)
        self.worker = spawn_task(self._save(patch, self._generation))

    async def _save(self, patch: SettingsPatch, generation: int) -> None:
        try:
            if patch != SettingsPatch():
                _ = await self.viewmodel.update(patch)
            if self._active and generation == self._generation:
                self.status_label.set_label(translate("prefs.saved"))
        except Exception:  # noqa: BLE001 -- never publish an unconfirmed settings patch
            if self._active and generation == self._generation:
                self.status_label.set_label(translate("prefs.error"))
        finally:
            if self._active and generation == self._generation:
                self.save_button.set_sensitive(True)

    def _save_clicked(self, _button: Gtk.Button) -> None:
        self.save()

    def _closed(self, _dialog: Adw.Dialog) -> None:
        self._active = False
        self._generation += 1
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
