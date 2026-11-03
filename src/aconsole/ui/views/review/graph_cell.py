"""The graph column of one row. Lanes come from `logic/git_lanes`; here they get pixels and colors."""

from __future__ import annotations

from typing import Final, override

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk  # noqa: E402

from aconsole.ui.viewmodels.logic.git_lanes import LaneEdge, RowLayout  # noqa: E402

LANE_WIDTH: Final = 14
ROW_HEIGHT: Final = 36
_MARGIN: Final = 10
_DOT: Final = 4.0
_DIAMOND: Final = 5.0
# A ring in the background colour around each node, so the lane line stops at the dot instead of running through it.
_HALO: Final = 2.0
_LINE: Final = 1.6
_DASH: Final = (3.0, 3.0)
_MAX_LANES: Final = 24
# Readable on both the light and the dark view background. 0 is the base branch.
_PALETTE: Final = (
    "#7d8590",
    "#2f81f7",
    "#d9730d",
    "#2da44e",
    "#bf3989",
    "#8250df",
    "#0e8a9e",
    "#c9482b",
)


def _rgba(spec: str) -> Gdk.RGBA:
    color = Gdk.RGBA()
    _ = color.parse(spec)
    return color


_COLORS: Final = tuple(_rgba(spec) for spec in _PALETTE)


def lane_x(lane: int) -> float:
    return _MARGIN + lane * LANE_WIDTH


def _edge(snapshot: Gtk.Snapshot, edge: LaneEdge, top: float, bottom: float) -> None:
    builder = Gsk.PathBuilder.new()
    start = lane_x(edge.start)
    end = lane_x(edge.end)
    builder.move_to(start, top)
    if edge.start == edge.end:
        builder.line_to(end, bottom)
    else:
        middle = (top + bottom) / 2
        builder.cubic_to(start, middle, end, middle, end, bottom)
    stroke = Gsk.Stroke.new(_LINE)
    if edge.dashed:
        stroke.set_dash(list(_DASH))
    snapshot.append_stroke(builder.to_path(), stroke, _COLORS[edge.color % len(_COLORS)])


class GraphCell(Gtk.Widget):
    """Rows are recycled, so `set_row` is all that changes. The list can be any length."""

    def __init__(self) -> None:
        super().__init__()
        self.row: RowLayout | None = None
        self.snapshot_node: bool = False
        self.uncommitted: bool = False

    def set_row(self, row: RowLayout, *, snapshot_node: bool, uncommitted: bool) -> None:
        self.row = row
        self.snapshot_node = snapshot_node
        self.uncommitted = uncommitted
        self.set_size_request(int(lane_x(min(row.width, _MAX_LANES))) + _MARGIN // 2, ROW_HEIGHT)
        self.queue_draw()

    @override
    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        row = self.row
        if row is None:
            return
        height = float(self.get_height())
        middle = height / 2
        for edge in row.above:
            _edge(snapshot, edge, 0.0, middle)
        for edge in row.below:
            _edge(snapshot, edge, middle, height)
        self._node(snapshot, row, middle)

    def _node(self, snapshot: Gtk.Snapshot, row: RowLayout, middle: float) -> None:
        color = _COLORS[row.color % len(_COLORS)]
        x = lane_x(row.lane)
        builder = Gsk.PathBuilder.new()
        if self.snapshot_node:
            builder.move_to(x, middle - _DIAMOND)
            builder.line_to(x + _DIAMOND, middle)
            builder.line_to(x, middle + _DIAMOND)
            builder.line_to(x - _DIAMOND, middle)
            builder.close()
        else:
            builder.add_circle(Graphene.Point().init(x, middle), _DOT)
        path = builder.to_path()
        _found, background = self.get_style_context().lookup_color("view_bg_color")
        if _found:
            snapshot.append_stroke(path, Gsk.Stroke.new(_HALO * 2), background)
        if self.uncommitted:
            snapshot.append_stroke(path, Gsk.Stroke.new(_LINE), color)
        else:
            snapshot.append_fill(path, Gsk.FillRule.WINDING, color)
