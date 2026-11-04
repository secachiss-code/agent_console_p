"""Copy and paste in the terminal are done by the terminal itself."""

from __future__ import annotations

from types import SimpleNamespace

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk  # noqa: E402

from aconsole.ui.views.terminal.page import TerminalPage  # noqa: E402

_CTRL = Gdk.ModifierType.CONTROL_MASK
_CTRL_SHIFT = _CTRL | Gdk.ModifierType.SHIFT_MASK


class _Terminal:
    def __init__(self, *, selection: bool) -> None:
        self.selection = selection
        self.calls: list[str] = []

    def get_has_selection(self) -> bool:
        return self.selection

    def copy_clipboard_format(self, _format: object) -> None:
        self.calls.append("copy")

    def paste_clipboard(self) -> None:
        self.calls.append("paste")

    def unselect_all(self) -> None:
        self.calls.append("unselect")


def _press(key: int, state: Gdk.ModifierType, *, selection: bool = False, text: bool = True) -> tuple[bool, list[str]]:
    terminal = _Terminal(selection=selection)
    page = SimpleNamespace(terminal=terminal, _clipboard_has_text=lambda: text, _search_focused=lambda: False)
    return TerminalPage._key_pressed(page, None, key, 0, state), terminal.calls  # type: ignore[arg-type]


def test_copy_keys() -> None:
    assert _press(Gdk.KEY_C, _CTRL_SHIFT) == (True, ["copy"])
    assert _press(Gdk.KEY_c, _CTRL, selection=True) == (True, ["copy", "unselect"])


def test_ctrl_c_without_a_selection_stays_an_interrupt() -> None:
    assert _press(Gdk.KEY_c, _CTRL) == (False, [])


def test_paste_keys() -> None:
    assert _press(Gdk.KEY_V, _CTRL_SHIFT) == (True, ["paste"])
    assert _press(Gdk.KEY_v, _CTRL) == (True, ["paste"])


def test_ctrl_v_with_an_image_goes_to_the_agent() -> None:
    assert _press(Gdk.KEY_v, _CTRL, text=False) == (False, [])
