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


def test_unmapped_rows_render_only_the_latest_binding_on_map(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    from aconsole.contracts.git import GitNodeView
    from aconsole.ui.viewmodels.git_vm import GraphBuilder

    Gtk.init()
    row = _RowWidget()
    builder = GraphBuilder(None)
    nodes = tuple(GitNodeView(
        oid=str(index) * 40, kind="commit", parents=(), refs=(), subject=f"commit {index}",
        author="author", at=datetime(2026, 11, 8, tzinfo=UTC), tab_id=None, plan_node=None,
    ) for index in (1, 2))
    first, second = (builder.commit(node) for node in nodes)
    mapped = [False]
    monkeypatch.setattr(row, "get_mapped", lambda: mapped[0])
    row.show_row(first, None)
    row.show_row(second, None)
    assert row.title.get_text() == ""
    assert row.cell.row is second.layout  # geometry is available before labels are mapped
    row._mapped_row(row)
    assert row.title.get_text() == "commit 2"
    assert row._tooltip_text.endswith("author")
    mapped[0] = True
    row.show_row(first, None)
    assert row.title.get_text() == "commit 1"
    mapped[0] = False
    row.show_row(second, None)
    row.clear_row()
    row._mapped_row(row)
    assert row.title.get_text() == "commit 1"  # unbound data must not be rendered later
    assert row._pending is None
