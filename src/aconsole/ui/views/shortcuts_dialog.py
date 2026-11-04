"""The list of shortcuts as they are bound now, overrides included."""

from __future__ import annotations

import locale
from typing import Final

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from aconsole.ui.viewmodels.logic.keymap import BindingSpec  # noqa: E402

_TITLE: Final = ("Keyboard shortcuts", "Горячие клавиши")
ACTION_TITLES: Final[dict[str, tuple[str, str]]] = {
    "palette": ("Command palette", "Командная палитра"),
    "new-tab": ("New tab", "Новая вкладка"),
    "close-tab": ("Close tab", "Закрыть вкладку"),
    "next-tab": ("Next tab", "Следующая вкладка"),
    "previous-tab": ("Previous tab", "Предыдущая вкладка"),
    **{f"tab-{number}": (f"Tab {number}", f"Вкладка {number}") for number in range(1, 10)},
    "next-attention": ("Next request for attention", "Следующий запрос внимания"),
    "approve": ("Allow the request", "Разрешить запрос"),
    "deny": ("Deny the request", "Отклонить запрос"),
    "attention-next": ("Next request, in the list", "Следующий запрос в списке"),
    "attention-previous": ("Previous request, in the list", "Предыдущий запрос в списке"),
    "attention-allow": ("Allow, in the list", "Разрешить в списке"),
    "attention-deny": ("Deny, in the list", "Отклонить в списке"),
    "attention-open": ("Open the request's tab, in the list", "Открыть вкладку запроса из списка"),
    "diff": ("Changes", "Изменения"),
    "git": ("Git", "Git"),
    "terminal-search": ("Search the terminal", "Поиск в терминале"),
    "copy": ("Copy", "Копировать"),
    "paste": ("Paste", "Вставить"),
    "toggle-sidebar": ("Show or hide the sidebar", "Показать или скрыть сайдбар"),
    "settings": ("Preferences", "Настройки"),
    "shortcuts": ("Keyboard shortcuts", "Горячие клавиши"),
    "zoom-in": ("Larger terminal text", "Крупнее текст терминала"),
    "zoom-reset": ("Normal terminal text", "Обычный текст терминала"),
    "zoom-out": ("Smaller terminal text", "Мельче текст терминала"),
    "quit": ("Quit", "Выход"),
    "send-next-key": ("Send the next key to the terminal", "Передать следующую клавишу терминалу"),
}


def _text(pair: tuple[str, str]) -> str:
    return pair[1 if (locale.getlocale()[0] or "").startswith("ru") else 0]


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
