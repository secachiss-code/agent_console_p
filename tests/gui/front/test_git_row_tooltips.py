"""Recycled Git rows keep tooltips current without querying the whole display."""

from typing import TYPE_CHECKING

import gi

if TYPE_CHECKING:
    import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from aconsole.ui.views.review.git_pane import _RowWidget  # noqa: E402


def test_recycled_tooltip_is_current_for_pointer_and_keyboard(monkeypatch: pytest.MonkeyPatch) -> None:
    Gtk.init()
    row = _RowWidget()
    shown: list[str] = []
    queries: list[Gtk.Widget] = []
    monkeypatch.setattr(Gtk.Tooltip, "set_text", lambda _tip, text: shown.append(text))
    def trigger(widget: Gtk.Widget) -> None:
        queries.append(widget)

    monkeypatch.setattr(Gtk.Widget, "trigger_tooltip_query", trigger)
    tooltip = Gtk.Tooltip()
    row._set_tooltip("first <author>")
    assert row.title.get_has_tooltip()
    assert Gtk.test_accessible_has_property(row.title, Gtk.AccessibleProperty.DESCRIPTION)
    assert row.title.emit("query-tooltip", 0, 0, False, tooltip)  # noqa: FBT003 -- GTK signal signature
    row._set_tooltip("second & author")
    assert row.title.emit("query-tooltip", 0, 0, True, tooltip)  # noqa: FBT003 -- GTK signal signature
    assert shown == ["first <author>", "second & author"]
    assert not queries
    row._tooltip_enter(Gtk.EventControllerMotion(), 0.0, 0.0)
    row._set_tooltip("third")
    assert queries == [row.title]
    row._set_tooltip("third")
    assert queries == [row.title]
    row._tooltip_leave(Gtk.EventControllerMotion())
    row._set_tooltip("fourth")
    assert queries == [row.title]
