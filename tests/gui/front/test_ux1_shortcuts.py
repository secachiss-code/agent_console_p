"""Every shortcut in the keymap does something, and one that cannot leaves its key alone."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from gi.repository import GLib, Gtk, Vte  # noqa: E402

from aconsole.ui.viewmodels.logic.keymap import DEFAULT_BINDINGS  # noqa: E402
from aconsole.ui.views import window as window_module  # noqa: E402
from aconsole.ui.views.shortcuts_dialog import ACTION_TITLES  # noqa: E402
from aconsole.ui.views.terminal.search import TerminalSearchBar  # noqa: E402
from aconsole.ui.views.window import MainWindow  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable


class _Stub:
    """`action_handlers` only reads attributes; each one stands for a method of the window."""

    def __getattr__(self, name: str) -> object:
        return lambda *_args: (lambda: True)


def _handled() -> set[str]:
    return set(MainWindow.action_handlers(_Stub()))  # type: ignore[arg-type]


def test_every_action_in_the_keymap_has_a_handler() -> None:
    bound = {binding.action_id for binding in DEFAULT_BINDINGS}
    assert bound - _handled() == set()


def test_no_handler_is_left_without_a_key() -> None:
    bound = {binding.action_id for binding in DEFAULT_BINDINGS}
    assert _handled() - bound == set()


def test_every_action_has_a_title_in_the_list_of_shortcuts() -> None:
    assert {binding.action_id for binding in DEFAULT_BINDINGS} - set(ACTION_TITLES) == set()


def test_an_action_unknown_to_the_window_takes_no_key() -> None:
    stub = _Stub()
    stub.action_handlers = dict  # type: ignore[method-assign]
    assert MainWindow._handle_action(stub, "palette") is False  # type: ignore[arg-type]


def test_handlers_named_in_the_table_exist_on_the_window() -> None:
    tree = ast.parse(Path(window_module.__file__).read_text(encoding="utf-8"))
    table = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "action_handlers"
    )
    named = {
        node.attr for node in ast.walk(table)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self"
    }
    assert {name for name in named if not hasattr(MainWindow, name)} == set()


class _Page:
    def __init__(self, *, focused: bool) -> None:
        self.focused = focused
        self.calls: list[str] = []

    def has_terminal_focus(self) -> bool:
        return self.focused

    def copy_selection(self) -> None:
        self.calls.append("copy")

    def paste_clipboard(self) -> None:
        self.calls.append("paste")


def _clipboard_key(method: str, page: _Page | None) -> bool:
    return getattr(MainWindow, method)(SimpleNamespace(_selected_page=lambda: page))


def test_copy_and_paste_act_on_a_focused_terminal() -> None:
    page = _Page(focused=True)
    assert _clipboard_key("_copy", page) is True
    assert _clipboard_key("_paste", page) is True
    assert page.calls == ["copy", "paste"]


def test_copy_and_paste_leave_the_key_to_any_other_widget() -> None:
    page = _Page(focused=False)
    assert _clipboard_key("_copy", page) is False
    assert _clipboard_key("_paste", page) is False
    assert _clipboard_key("_copy", None) is False
    assert page.calls == []


def _spin_until(ready: Callable[[], bool]) -> bool:
    deadline = GLib.get_monotonic_time() + 5_000_000
    while not ready() and GLib.get_monotonic_time() < deadline:
        _ = GLib.MainContext.default().iteration(False)  # noqa: FBT003 -- GLib takes this positionally
    return ready()


def test_search_before_the_terminal_is_shown_does_not_crash() -> None:
    bar = TerminalSearchBar(Vte.Terminal(), lambda: None)
    assert bar.search("anything") is False
    bar.entry.set_text("anything")
    bar._next()
    bar._previous()
    bar._closed()


def test_search_finds_literal_text_and_says_when_it_does_not() -> None:
    terminal = Vte.Terminal()
    closed: list[bool] = []
    bar = TerminalSearchBar(terminal, lambda: closed.append(True))
    window = Gtk.Window(child=terminal, default_width=640, default_height=400)
    window.present()
    try:
        assert _spin_until(terminal.get_realized)
        terminal.feed(b"one (two) three\r\n")
        # VTE reads what it was fed from the main loop.
        assert _spin_until(lambda: bar.search("(TWO)"))
        assert not bar.entry.has_css_class("error")
        # The same text again, and stepping past the only match, still find it.
        assert bar.search("(TWO)") is True
        bar.entry.set_text("(TWO)")
        bar._next()
        bar._previous()
        assert not bar.entry.has_css_class("error")
        assert bar.search("t.o") is False
        assert bar.entry.has_css_class("error")
        assert bar.search("") is False
        assert not bar.entry.has_css_class("error")
        bar._closed()
        assert closed == [True]
        assert bar.get_reveal_child() is False
    finally:
        window.destroy()
