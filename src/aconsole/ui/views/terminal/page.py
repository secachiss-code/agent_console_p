"""One lazily launched terminal, retained across tab selection changes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gio, Gtk, Pango, Vte  # noqa: E402

from aconsole.contracts.common import TabId  # noqa: E402
from aconsole.contracts.settings import Settings  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.viewmodels.terminal_vm import TerminalViewModel  # noqa: E402
from aconsole.ui.views.terminal.messages import _ as translate  # noqa: E402
from aconsole.ui.views.terminal.palette import TerminalPaletteBinding  # noqa: E402
from aconsole.ui.views.terminal.session import TerminalSession  # noqa: E402


class TerminalPage(Gtk.Overlay):
    def __init__(
        self, client: CoreClient, tab_id: TabId, on_new: Callable[[], None], on_close: Callable[[str], None],
    ) -> None:
        super().__init__()
        self.tab_id: TabId = tab_id
        self.on_new: Callable[[], None] = on_new
        self._on_close: Callable[[str], None] = on_close
        self._closed: bool = False
        self._restart_task: asyncio.Task[None] | None = None
        self.viewmodel: TerminalViewModel = TerminalViewModel(client, tab_id)
        self.terminal: Vte.Terminal = Vte.Terminal()
        self.terminal.set_hexpand(True)
        self.terminal.set_vexpand(True)
        # Installed VTE stubs derive from Gtk3.Widget; runtime VTE 0.76 is Gtk4.
        terminal_widget: Gtk.Widget = self.terminal  # pyright: ignore[reportAssignmentType] -- VTE Gtk3 stub base mismatch
        terminal_widget.add_css_class("ac-terminal")
        self.set_child(terminal_widget)
        self.palette: TerminalPaletteBinding = TerminalPaletteBinding(self.terminal)
        self.session: TerminalSession = TerminalSession(self.terminal, self.viewmodel)
        self.worker: asyncio.Task[None] | None = None
        self._settings: Settings | None = None
        self.font_description: Pango.FontDescription | None = None
        self._setup_overlays()

    def _setup_overlays(self) -> None:
        self.error_label: Gtk.Label = Gtk.Label(wrap=True)
        self.error_label.set_halign(Gtk.Align.CENTER)
        self.error_label.set_valign(Gtk.Align.START)
        self.error_label.add_css_class("ac-session-error")
        self.error_label.set_visible(False)
        self.add_overlay(self.error_label)
        self.ended_revealer: Gtk.Revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.CROSSFADE, transition_duration=200,
        )
        self.ended_revealer.set_halign(Gtk.Align.CENTER)
        self.ended_revealer.set_valign(Gtk.Align.CENTER)
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        panel.add_css_class("ac-session-ended")
        title = Gtk.Label(label=translate("terminal.ended"))
        title.add_css_class("title-4")
        panel.append(title)
        self.ended_actions: Gtk.Box = Gtk.Box(spacing=8)
        for key, callback in (
            ("terminal.resume", self._resume), ("terminal.new", self._new_session), ("terminal.close", self._close_tab),
        ):
            button = Gtk.Button(label=translate(key))
            button.add_css_class("suggested-action" if key == "terminal.resume" else "flat")
            _ = button.connect("clicked", callback)
            self.ended_actions.append(button)
        panel.append(self.ended_actions)
        self.ended_revealer.set_child(panel)
        self.add_overlay(self.ended_revealer)

    def start(self) -> None:
        if not self._closed and self.worker is None:
            self.worker = spawn_task(self._observe(self.session))

    async def _observe(self, session: TerminalSession) -> None:
        if self._closed or session is not self.session:
            return
        try:
            _ = await session.start()
            if self._settings is not None and not self._closed:
                self.apply_settings(self._settings)
            started_error = await session.started_report()
            if started_error is not None and not self._closed and session is self.session:
                self._show_error(translate("terminal.report_error"))
            _started_error, exited_error = await session.wait_finished()
            if self._closed or session is not self.session:
                return
            if exited_error is not None:
                self._show_error(translate("terminal.report_error"))
            self.ended_revealer.set_reveal_child(True)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 -- a failed spawn stays visible and never invents a fallback command
            if not self._closed and session is self.session:
                self._show_error(translate("terminal.error").format(error=error))

    def _show_error(self, message: str) -> None:
        self.error_label.set_label(message)
        self.error_label.set_visible(True)

    def apply_settings(self, settings: Settings) -> None:
        if self._closed:
            return
        self._settings = settings
        family = settings.terminal_font or self._system_font()
        description = Pango.FontDescription.from_string(family)
        description.set_size(settings.terminal_font_size * Pango.SCALE)
        self.font_description = description
        self.terminal.set_font(description)
        self.terminal.set_scrollback_lines(settings.scrollback_lines)

    @staticmethod
    def _system_font() -> str:
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup("org.gnome.desktop.interface", recursive=True) if source is not None else None
        if schema is not None:
            settings = Gio.Settings.new_full(schema, None, None)
            return settings.get_string("monospace-font-name") or "Monospace"
        return "Monospace"

    def _resume(self, _button: Gtk.Button) -> None:
        self._request_restart(resume=True)

    def _new_session(self, _button: Gtk.Button) -> None:
        self._request_restart(resume=False)

    def _request_restart(self, *, resume: bool) -> None:
        if self._closed or (self._restart_task is not None and not self._restart_task.done()):
            return
        self._restart_task = spawn_task(self._restart(resume=resume))

    async def _restart(self, *, resume: bool) -> None:
        self.ended_actions.set_sensitive(False)
        try:
            _ = await self.session.wait_finished()
            _ = await self.viewmodel.restart(resume=resume)
            if self._closed:
                return
            self.ended_revealer.set_reveal_child(False)
            self.error_label.set_visible(False)
            self.session = TerminalSession(self.terminal, self.viewmodel)
            self.worker = None
            self.start()
        except Exception:  # noqa: BLE001 -- keep the ended session available after a failed restart
            if not self._closed:
                self._show_error(translate("terminal.restart_error"))
        finally:
            if not self._closed:
                self.ended_actions.set_sensitive(True)

    def _close_tab(self, _button: Gtk.Button) -> None:
        if not self._closed:
            self._on_close(str(self.tab_id))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _ = self.session.cancel_pending_spawn()
        self.palette.close()
