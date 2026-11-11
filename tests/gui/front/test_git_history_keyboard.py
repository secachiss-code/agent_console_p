"""Native history navigation keeps selection and the inspector in sync."""

# Shared native-input and real-core fixtures intentionally expose private helpers.
# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk  # noqa: E402

from aconsole.ui.views.review.git_pane import RowItem  # noqa: E402
from tests.gui.front.test_b1_keys import _XTestKeyboard  # noqa: E402
from tests.gui.front.test_git_pane import _on_pane, _project, _settled  # noqa: E402

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from gi.repository import Adw

    from aconsole.ui.views.review.git_pane import GitPane


def test_native_history_keys_update_selection_and_inspector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _tree = _project(tmp_path)

    async def check(pane: GitPane, _toasts: list[str], window: Adw.ApplicationWindow) -> None:
        await _settled(lambda: pane.store.get_n_items() >= 4)
        count = pane.store.get_n_items()
        pane.selection.set_selected(0)
        assert pane.list_view.grab_focus()
        keyboard = _XTestKeyboard(window)
        try:
            for key, position in (
                (Gdk.KEY_Down, 1),
                (Gdk.KEY_Up, 0),
                (Gdk.KEY_End, count - 1),
                (Gdk.KEY_Home, 0),
            ):
                keyboard.press(key)
                await _settled(lambda position=position: pane.selection.get_selected() == position)
                item = pane.selection.get_selected_item()
                assert isinstance(item, RowItem)
                await _settled(lambda key=item.row.key: pane.selected_key == key)
                assert pane.source is not None
        finally:
            keyboard.close()

    _on_pane(tmp_path, monkeypatch, repo, check)
