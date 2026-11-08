"""Rebinding identical graph geometry preserves GTK's retained snapshot."""

from dataclasses import replace
from typing import TYPE_CHECKING

import gi

if TYPE_CHECKING:
    import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from aconsole.ui.viewmodels.logic.git_lanes import LaneEdge, RowLayout  # noqa: E402
from aconsole.ui.views.review.graph_cell import GraphCell  # noqa: E402


def test_rebinding_only_redraws_visual_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    Gtk.init()
    cell = GraphCell()
    draws: list[Gtk.Widget] = []

    def draw(widget: Gtk.Widget) -> None:
        draws.append(widget)

    monkeypatch.setattr(Gtk.Widget, "queue_draw", draw)
    layout = RowLayout(lane=0, color=0, above=(LaneEdge(0, 0, 0),), below=(LaneEdge(0, 0, 0),))
    cell.set_row(layout, snapshot_node=False, uncommitted=False)
    equal = replace(layout)
    cell.set_row(equal, snapshot_node=False, uncommitted=False)
    assert cell.row is equal
    assert len(draws) == 1
    cell.set_row(equal, snapshot_node=True, uncommitted=False)
    cell.set_row(equal, snapshot_node=True, uncommitted=True)
    cell.set_row(replace(equal, color=1), snapshot_node=True, uncommitted=True)
    assert len(draws) == 4
