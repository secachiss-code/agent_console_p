"""One lazily launched terminal, retained across tab selection changes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte  # noqa: E402

from aconsole.contracts.common import TabId  # noqa: E402
from aconsole.contracts.settings import Settings  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.viewmodels.logic.fonts import font_families, pango_family  # noqa: E402
from aconsole.ui.viewmodels.terminal_vm import TerminalViewModel  # noqa: E402
from aconsole.ui.views.terminal.clipboard import TerminalClipboard  # noqa: E402
from aconsole.ui.views.terminal.messages import _ as translate  # noqa: E402
from aconsole.ui.views.terminal.palette import TerminalPaletteBinding  # noqa: E402
from aconsole.ui.views.terminal.session import TerminalSession  # noqa: E402


class TerminalPage(Gtk.Overlay):
    def __init__(
        self, client: CoreClient, tab_id: TabId, on_new: Callable[[], None], on_close: Callable[[str], None],
        *, on_error: Callable[[str], None] | None = None,
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
        # VTE scrolls by itself and shows no bar: this one reads the terminal's own position in its history.
        self.scrollbar: Gtk.Scrollbar = Gtk.Scrollbar(
            orientation=Gtk.Orientation.VERTICAL,
            adjustment=self.terminal.get_vadjustment(),  # pyright: ignore[reportArgumentType] -- VTE Gtk3 stub
        )
        self.scrollbar.add_css_class("ac-terminal-scrollbar")
        with_bar = Gtk.Box()
        with_bar.append(terminal_widget)
        with_bar.append(self.scrollbar)
        self.set_child(with_bar)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        _ = keys.connect("key-pressed", self._key_pressed)
        self.add_controller(keys)
        self.palette: TerminalPaletteBinding = TerminalPaletteBinding(self.terminal)
        self.session: TerminalSession = TerminalSession(self.terminal, self.viewmodel)
        self.worker: asyncio.Task[None] | None = None
        self._settings: Settings | None = None
        self.font_description: Pango.FontDescription | None = None
        self._setup_overlays()
        self.clipboard: TerminalClipboard = TerminalClipboard(
            self, self.terminal, self.viewmodel.prepare_image, on_error or self._show_error,
        )
        focus = Gtk.EventControllerFocus()
        _ = focus.connect("leave", self._cancel_clipboard)
        terminal_widget.add_controller(focus)
        _ = self.connect("unmap", self._cancel_clipboard)
        self._setup_clipboard_menu()

    def _cancel_clipboard(self, _source: object) -> None:
        self.clipboard.cancel()

    @staticmethod
    def _activate_clipboard(
        callback: Callable[[], None], _action: Gio.SimpleAction, _param: GLib.Variant | None,
    ) -> None:
        callback()

    def copy_selection(self) -> None:
        self.terminal.copy_clipboard_format(Vte.Format.TEXT)

    def _key_pressed(  # noqa: PLR0911 -- each consumed chord returns immediately
        self, _keys: Gtk.EventControllerKey, keyval: int, _code: int, state: Gdk.ModifierType,
    ) -> bool:
        """Copy and paste belong to the terminal: an agent in a container cannot reach the desktop clipboard."""
        if not self.terminal.has_focus():
            return False
        modifiers = state & Gtk.accelerator_get_default_mod_mask()
        key = Gdk.keyval_to_lower(keyval)
        with_shift = modifiers == Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        plain = modifiers == Gdk.ModifierType.CONTROL_MASK
        if key == Gdk.KEY_c and (with_shift or (plain and self.terminal.get_has_selection())):
            self.terminal.copy_clipboard_format(Vte.Format.TEXT)
            if plain:
                self.terminal.unselect_all()
            return True
        if key == Gdk.KEY_v and (with_shift or plain):
            self.clipboard.paste()
            return True
        if key == Gdk.KEY_Insert and modifiers == Gdk.ModifierType.SHIFT_MASK:
            self.clipboard.paste()
            return True
        if key == Gdk.KEY_Insert and plain:
            self.terminal.copy_clipboard_format(Vte.Format.TEXT)
            return True
        if key == Gdk.KEY_Menu or (key == Gdk.KEY_F10 and modifiers == Gdk.ModifierType.SHIFT_MASK):
            self._popup_clipboard()
            return True
        return False

    def _setup_clipboard_menu(self) -> None:
        actions = Gio.SimpleActionGroup()
        self._copy_action: Gio.SimpleAction = Gio.SimpleAction.new("copy", None)
        _ = self._copy_action.connect("activate", lambda *_args: self.terminal.copy_clipboard_format(Vte.Format.TEXT))
        actions.add_action(self._copy_action)
        for name, callback in (
            ("paste", lambda: self._menu_paste(image_only=False)),
            ("image", lambda: self._menu_paste(image_only=True)),
            ("select", self.terminal.select_all),
        ):
            action = Gio.SimpleAction.new(name, None)
            _ = action.connect("activate", partial(self._activate_clipboard, callback))
            actions.add_action(action)
        self.insert_action_group("terminal", actions)
        menu = Gio.Menu()
        for name in ("copy", "paste", "image", "select"):
            menu.append(translate(f"clipboard.{name}"), f"terminal.{name}")
        self._clipboard_menu: Gtk.PopoverMenu = Gtk.PopoverMenu.new_from_model(menu)
        self._clipboard_menu.set_parent(self)
        click = Gtk.GestureClick(button=3)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        _ = click.connect("pressed", self._clipboard_pressed)
        self.add_controller(click)

    def _clipboard_pressed(self, gesture: Gtk.GestureClick, _count: int, x: float, y: float) -> None:
        _ = gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._popup_clipboard(x, y)

    def _popup_clipboard(self, x: float = 0, y: float = 0) -> None:
        self._copy_action.set_enabled(self.terminal.get_has_selection())
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        self._clipboard_menu.set_pointing_to(rect)
        self._clipboard_menu.popup()

    def _menu_paste(self, *, image_only: bool) -> None:
        self._clipboard_menu.popdown()
        self.focus_terminal()
        # Restore keyboard focus before starting the asynchronous clipboard transfer.
        def paste() -> bool:
            self.clipboard.paste(image_only=image_only)
            return GLib.SOURCE_REMOVE
        _ = GLib.idle_add(paste)

    def _setup_overlays(self) -> None:
        self.error_label: Gtk.Label = Gtk.Label(wrap=True)
        self.error_label.set_halign(Gtk.Align.CENTER)
        self.error_label.set_valign(Gtk.Align.START)
        self.error_label.add_css_class("ac-session-error")
        self.error_label.set_visible(False)
        self.add_overlay(self.error_label)
        self.retry_button: Gtk.Button = Gtk.Button(label=translate("terminal.retry"), visible=False)
        self.retry_button.set_halign(Gtk.Align.CENTER)
        self.retry_button.set_valign(Gtk.Align.CENTER)
        _ = self.retry_button.connect("clicked", self._retry_start)
        self.add_overlay(self.retry_button)
        self.ended_revealer: Gtk.Revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.CROSSFADE, transition_duration=200,
        )
        self.ended_revealer.set_halign(Gtk.Align.CENTER)
        self.ended_revealer.set_valign(Gtk.Align.CENTER)
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        panel.add_css_class("ac-session-ended")
        self.ended_title: Gtk.Label = Gtk.Label(label=translate("terminal.ended"))
        self.ended_title.add_css_class("title-4")
        panel.append(self.ended_title)
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
            code = session.exit_code
            ended = translate("terminal.ended")
            self.ended_title.set_label(translate("terminal.ended_code").format(code=code) if code else ended)
            self.ended_revealer.set_reveal_child(True)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 -- a failed spawn stays visible and never invents a fallback command
            if not self._closed and session is self.session:
                self._show_error(translate("terminal.error").format(error=error))
                self.retry_button.set_visible(True)

    def _retry_start(self, _button: Gtk.Button) -> None:
        if self._closed or self.worker is None or not self.worker.done():
            return
        self.retry_button.set_visible(False)
        self.error_label.set_visible(False)
        self.session = TerminalSession(self.terminal, self.viewmodel)
        self.worker = None
        self.start()

    def _show_error(self, message: str) -> None:
        self.error_label.set_label(message)
        self.error_label.set_visible(True)

    def apply_settings(self, settings: Settings) -> None:
        if self._closed:
            return
        self._settings = settings
        families = font_families(settings.terminal_font)
        if families:
            # Several families: a glyph the first one lacks comes from the next, then from any monospace face.
            description = Pango.FontDescription()
            description.set_family(pango_family(families))
        else:
            description = Pango.FontDescription.from_string(self._system_font())
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

    def focus_terminal(self) -> None:
        widget: Gtk.Widget = self.terminal  # pyright: ignore[reportAssignmentType] -- VTE 0.76 is Gtk4 at runtime
        _ = widget.grab_focus()

    def inject(self, text: str, *, submit: bool) -> None:
        """Text the core sends to the agent: a bracketed paste, so its lines do not run as separate prompts."""
        if self._closed:
            return
        self.terminal.paste_text(text.rstrip("\n"))
        if submit:
            self.terminal.feed_child(b"\r")

    def _close_tab(self, _button: Gtk.Button) -> None:
        if not self._closed:
            self._on_close(str(self.tab_id))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.clipboard.closed = True
        self.clipboard.cancel()
        self._clipboard_menu.unparent()
        _ = self.session.cancel_pending_spawn()
        self.palette.close()
