"""Search the terminal's text and history. The typed text is searched as it is, not as a pattern."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, GLib, Gtk, Vte  # noqa: E402

from aconsole.ui.views.terminal.messages import _ as translate  # noqa: E402

# VTE compiles search patterns with PCRE2 and requires MULTILINE.
_PCRE2_CASELESS: Final = 0x00000008
_PCRE2_MULTILINE: Final = 0x00000400
_NOT_FOUND: Final = "error"


class TerminalSearchBar(Gtk.Revealer):
    def __init__(self, terminal: Vte.Terminal, on_close: Callable[[], None]) -> None:
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=150)
        self._terminal: Vte.Terminal = terminal
        self._on_close: Callable[[], None] = on_close
        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.START)
        self.entry: Gtk.SearchEntry = Gtk.SearchEntry(placeholder_text=translate("terminal.search"))
        self.entry.set_width_chars(24)
        _ = self.entry.connect("search-changed", self._changed)
        _ = self.entry.connect("activate", self._next)
        _ = self.entry.connect("next-match", self._next)
        _ = self.entry.connect("previous-match", self._previous)
        _ = self.entry.connect("stop-search", self._closed)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        _ = keys.connect("key-pressed", self._key_pressed)
        self.entry.add_controller(keys)
        box = Gtk.Box(spacing=4)
        box.add_css_class("ac-terminal-search")
        box.append(self.entry)
        for icon, key, callback in (
            ("go-up-symbolic", "terminal.search_previous", self._previous),
            ("go-down-symbolic", "terminal.search_next", self._next),
            ("window-close-symbolic", "terminal.search_close", self._closed),
        ):
            button = Gtk.Button(icon_name=icon, tooltip_text=translate(key))
            button.add_css_class("flat")
            button.update_property([Gtk.AccessibleProperty.LABEL], [translate(key)])
            _ = button.connect("clicked", callback)
            box.append(button)
        self.set_child(box)
        self.set_can_target(False)

    def open(self) -> None:
        self.set_reveal_child(True)
        self.set_can_target(True)
        _ = self.entry.grab_focus()
        self.entry.select_region(0, -1)

    def entry_focused(self) -> bool:
        root = self.get_root()
        focus = root.get_focus() if isinstance(root, Gtk.Window) else None
        return focus is not None and (focus is self.entry or focus.is_ancestor(self.entry))

    def search(self, text: str) -> bool:
        """Find `text` from the end of the history upward. False: nothing matched."""
        terminal_widget: Gtk.Widget = self._terminal  # pyright: ignore[reportAssignmentType] -- VTE 0.76 is Gtk4
        # VTE crashes when asked to search before it has been shown.
        if not terminal_widget.get_realized():
            return False
        if text == "":
            self._terminal.search_set_regex(None, 0)
            self.entry.remove_css_class(_NOT_FOUND)
            return False
        regex = Vte.Regex.new_for_search(GLib.Regex.escape_string(text, -1), -1, _PCRE2_MULTILINE | _PCRE2_CASELESS)
        self._terminal.search_set_regex(regex, 0)
        self._terminal.search_set_wrap_around(True)  # noqa: FBT003 -- VTE takes this positionally
        # VTE searches from the selection: the last match would hide the same text as it is typed on.
        self._terminal.unselect_all()
        return self._show(found=self._terminal.search_find_previous())

    def _show(self, *, found: bool) -> bool:
        if found:
            self.entry.remove_css_class(_NOT_FOUND)
        else:
            self.entry.add_css_class(_NOT_FOUND)
        return found

    def _changed(self, entry: Gtk.SearchEntry) -> None:
        _ = self.search(entry.get_text())

    def _can_step(self) -> bool:
        terminal_widget: Gtk.Widget = self._terminal  # pyright: ignore[reportAssignmentType] -- VTE 0.76 is Gtk4
        return self.entry.get_text() != "" and terminal_widget.get_realized()

    def _next(self, *_args: object) -> None:
        if self._can_step():
            # The only match is already selected: VTE finds no other, but the text is there.
            _ = self._show(found=self._terminal.search_find_next() or self._terminal.get_has_selection())

    def _previous(self, *_args: object) -> None:
        if self._can_step():
            _ = self._show(found=self._terminal.search_find_previous() or self._terminal.get_has_selection())

    def _closed(self, *_args: object) -> None:
        self.set_reveal_child(False)
        self.set_can_target(False)
        terminal_widget: Gtk.Widget = self._terminal  # pyright: ignore[reportAssignmentType] -- VTE 0.76 is Gtk4
        if terminal_widget.get_realized():
            self._terminal.search_set_regex(None, 0)
            self._terminal.unselect_all()
        self._on_close()

    def _key_pressed(self, _keys: Gtk.EventControllerKey, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if keyval in {Gdk.KEY_Return, Gdk.KEY_KP_Enter} and state & Gdk.ModifierType.SHIFT_MASK:
            self._previous()
            return True
        return False
