"""The list of shortcuts as they are bound now, overrides included."""

from __future__ import annotations

from typing import Final

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from aconsole.ui.i18n import _  # noqa: E402
from aconsole.ui.viewmodels.logic.keymap import BindingSpec  # noqa: E402

_TITLE: Final = "shortcuts.title"
ACTION_TITLES: Final[dict[str, str]] = {
    "palette": "shortcuts.palette",
    "new-tab": "shortcuts.new-tab",
    "close-tab": "shortcuts.close-tab",
    "next-tab": "shortcuts.next-tab",
    "previous-tab": "shortcuts.previous-tab",
    "tab-1": "shortcuts.tab-1",
    "tab-2": "shortcuts.tab-2",
    "tab-3": "shortcuts.tab-3",
    "tab-4": "shortcuts.tab-4",
    "tab-5": "shortcuts.tab-5",
    "tab-6": "shortcuts.tab-6",
    "tab-7": "shortcuts.tab-7",
    "tab-8": "shortcuts.tab-8",
    "tab-9": "shortcuts.tab-9",
    "next-attention": "shortcuts.next-attention",
    "approve": "shortcuts.approve",
    "deny": "shortcuts.deny",
    "attention-next": "shortcuts.attention-next",
    "attention-previous": "shortcuts.attention-previous",
    "attention-allow": "shortcuts.attention-allow",
    "attention-deny": "shortcuts.attention-deny",
    "attention-open": "shortcuts.attention-open",
    "diff": "shortcuts.diff",
    "git": "shortcuts.git",
    "terminal-search": "shortcuts.terminal-search",
    "copy": "shortcuts.copy",
    "paste": "shortcuts.paste",
    "toggle-sidebar": "shortcuts.toggle-sidebar",
    "settings": "shortcuts.settings",
    "shortcuts": "shortcuts.shortcuts",
    "zoom-in": "shortcuts.zoom-in",
    "zoom-reset": "shortcuts.zoom-reset",
    "zoom-out": "shortcuts.zoom-out",
    "quit": "shortcuts.quit",
    "send-next-key": "shortcuts.send-next-key",
}


def _text(key: str) -> str:
    return _(key)


def _keys(accelerators: tuple[str, ...]) -> str:
    labels: list[str] = []
    for accelerator in accelerators:
        parsed, key, modifiers = Gtk.accelerator_parse(accelerator)
        labels.append(Gtk.accelerator_get_label(key, modifiers) if parsed else accelerator)
    return ", ".join(labels)


class ShortcutsDialog(Adw.Dialog):
    def __init__(self, bindings: tuple[BindingSpec, ...]) -> None:
        super().__init__(title=_text(_TITLE), content_width=480, content_height=560)
        group = Adw.PreferencesGroup()
        self.rows: dict[str, Adw.ActionRow] = {}
        for binding in bindings:
            row = Adw.ActionRow(title=_text(ACTION_TITLES[binding.action_id]))
            row.add_suffix(Gtk.Label(label=_keys(binding.accelerators), xalign=1))
            group.add(row)
            self.rows[binding.action_id] = row
        page = Adw.PreferencesPage()
        page.add(group)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(page)
        self.set_child(toolbar)
