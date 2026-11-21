"""A shared, keyboard-accessible node-and-link canvas for the project graphs."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from textwrap import shorten

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk  # noqa: E402

from aconsole.contracts.common import NodeStatus  # noqa: E402
from aconsole.ui.i18n import _ as translate  # noqa: E402

_WIDTH = 680
_HEIGHT = 460
_MID_X = _WIDTH / 2
_MID_Y = _HEIGHT / 2
_RADIUS = 11
_CLICK_RADIUS = 28
_DRAG_THRESHOLD = 6
_LABELS = 45
_LINK_DASHES: dict[str, tuple[float, ...]] = {"depends_on": (), "decomposes": (6, 4), "related": (2, 4)}
_STATUS_COLORS = {
    "pending": "window_fg_color",
    "in_progress": "accent_bg_color",
    "verifying": "accent_bg_color",
    "done": "success_color",
    "blocked": "warning_color",
    "failed": "error_color",
    "cancelled": "window_fg_color",
}


@dataclass(frozen=True, slots=True)
class Point:
    id: str
    label: str
    group: bool = False
    state: str = ""
    description: str = ""


@dataclass(frozen=True, slots=True)
class Link:
    source: str
    target: str
    kind: str


class NetworkMap(Gtk.Box):
    def __init__(self, on_select: Callable[[str], None], *, plan_legend: bool = False) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._on_select: Callable[[str], None] = on_select
        self._points: tuple[Point, ...] = ()
        self._links: tuple[Link, ...] = ()
        self._positions: dict[str, tuple[float, float]] = {}
        self._neighbors: set[str] = set()
        self._selected: str | None = None
        self._zoom: float = 1
        self._updating: bool = False
        toolbar = Gtk.Box(spacing=6)
        self._choices: Gtk.DropDown = Gtk.DropDown.new_from_strings((translate("graph.select"),))
        self._choices.set_enable_search(True)
        self._choices.set_hexpand(True)
        self._choices.set_tooltip_text(translate("graph.select"))
        _ = self._choices.connect("notify::selected", self._choose)
        self._choices.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [GObject.Value(GObject.TYPE_STRING, translate("graph.select"))],
        )
        toolbar.append(self._choices)
        for value, icon, label in (
            (-1, "zoom-out-symbolic", "graph.zoom_out"),
            (1, "zoom-in-symbolic", "graph.zoom_in"),
        ):
            button = Gtk.Button(icon_name=icon, tooltip_text=translate(label))
            _ = button.connect("clicked", self._scale, value)
            toolbar.append(button)
        if plan_legend:
            toolbar.append(_legend())
        self.append(toolbar)
        self._area: Gtk.DrawingArea = Gtk.DrawingArea(content_width=_WIDTH, content_height=_HEIGHT)
        self._area.set_halign(Gtk.Align.CENTER)
        self._area.set_valign(Gtk.Align.CENTER)
        self._area.set_draw_func(self._draw)
        click = Gtk.GestureClick()
        _ = click.connect("released", self._hit)
        self._area.add_controller(click)
        self._scroll: Gtk.ScrolledWindow = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self._scroll.set_child(self._area)
        self.append(self._scroll)
        self._drag_start: tuple[float, float] = (0, 0)
        self._dragged: bool = False
        drag = Gtk.GestureDrag()
        _ = drag.connect("drag-begin", self._pan_start)
        _ = drag.connect("drag-update", self._pan)
        _ = drag.connect("drag-end", self._pan_end)
        self._area.add_controller(drag)

    def set_graph(self, points: tuple[Point, ...], links: tuple[Link, ...]) -> None:
        if points == self._points and links == self._links:
            return
        old_ids = tuple(point.id for point in self._points)
        self._points, self._links = points, links
        if old_ids != tuple(point.id for point in points):
            self._positions = _positions(points)
            self._updating = True
            labels = (f"{point.label} · {point.description}" if point.description else point.label for point in points)
            self._choices.set_model(Gtk.StringList.new((translate("graph.select"), *labels)))
            self._updating = False
        self.highlight(self._selected)

    def highlight(self, node_id: str | None) -> None:
        self._selected = node_id
        selected = next((index for index, point in enumerate(self._points, start=1) if point.id == node_id), 0)
        self._updating = True
        self._choices.set_selected(selected)
        self._updating = False
        self._neighbors = {
            link.target if link.source == node_id else link.source
            for link in self._links
            if node_id in (link.source, link.target)
        }
        self._area.queue_draw()

    def _scale(self, _button: Gtk.Button, direction: int) -> None:
        self._zoom = max(0.65, min(2.5, self._zoom + 0.2 * direction))
        self._area.set_content_width(round(_WIDTH * self._zoom))
        self._area.set_content_height(round(_HEIGHT * self._zoom))
        self._area.queue_draw()

    def _choose(self, dropdown: Gtk.DropDown, _param: GObject.ParamSpec) -> None:
        index = dropdown.get_selected()
        if not self._updating and 1 <= index <= len(self._points):
            self._on_select(self._points[index - 1].id)

    def _pan_start(self, _drag: Gtk.GestureDrag, _x: float, _y: float) -> None:
        self._dragged = False
        self._drag_start = (self._scroll.get_hadjustment().get_value(), self._scroll.get_vadjustment().get_value())

    def _pan(self, drag: Gtk.GestureDrag, dx: float, dy: float) -> None:
        if not self._dragged and math.hypot(dx, dy) < _DRAG_THRESHOLD:
            return
        self._dragged = True
        _ = drag.set_state(Gtk.EventSequenceState.CLAIMED)
        self._area.set_cursor_from_name("grabbing")
        self._scroll.get_hadjustment().set_value(self._drag_start[0] - dx)
        self._scroll.get_vadjustment().set_value(self._drag_start[1] - dy)

    def _pan_end(self, _drag: Gtk.GestureDrag, _dx: float, _dy: float) -> None:
        self._area.set_cursor_from_name(None)

    def _hit(self, _gesture: Gtk.GestureClick, _presses: int, x: float, y: float) -> None:
        if self._dragged:
            return
        distances = (
            (math.hypot(cx - x / self._zoom, cy - y / self._zoom), identifier)
            for identifier, (cx, cy) in self._positions.items()
        )
        distance, identifier = min(distances, default=(math.inf, ""))
        if distance <= _CLICK_RADIUS:
            self._on_select(identifier)

    def _draw(self, _area: Gtk.DrawingArea, ctx: cairo.Context[cairo.Surface], _width: int, _height: int) -> None:
        style = self.get_style_context()
        _, foreground = style.lookup_color("window_fg_color")
        _, accent = style.lookup_color("accent_bg_color")
        ctx.scale(self._zoom, self._zoom)
        for link in self._links:
            source, target = self._positions.get(link.source), self._positions.get(link.target)
            if source is None or target is None:
                continue
            active = self._selected in (link.source, link.target)
            alpha = 0.85 if active else 0.32 if self._selected is None else 0.1
            color = accent if active else foreground
            ctx.set_source_rgba(color.red, color.green, color.blue, alpha)
            ctx.set_line_width(2 if active else 1)
            ctx.set_dash(_LINK_DASHES.get(link.kind, ()))
            ctx.move_to(*source)
            ctx.line_to(*target)
            ctx.stroke()
        ctx.set_dash(())
        for point in self._points:
            self._draw_point(ctx, point)

    def _draw_point(self, ctx: cairo.Context[cairo.Surface], point: Point) -> None:
        style = self.get_style_context()
        selected = point.id == self._selected
        label = selected or point.id in self._neighbors or len(self._points) <= _LABELS
        _, foreground = style.lookup_color("window_fg_color")
        x, y = self._positions[point.id]
        radius = _RADIUS + (6 if point.group else 0)
        alpha = 1 if selected else 0.78 if self._selected is None or point.id in self._neighbors else 0.4
        _draw_marker(style, ctx, x, y, radius, point.state, alpha, hollow=point.group and not selected)
        if label:
            text = shorten(point.label, width=24, placeholder="…")
            ctx.set_source_rgba(foreground.red, foreground.green, foreground.blue, alpha)
            ctx.set_font_size(12)
            ctx.move_to(x - ctx.text_extents(text).width / 2, y + radius + 15)
            ctx.show_text(text)


def _draw_marker(  # noqa: PLR0913, PLR0917 -- a Cairo marker with position and styling
    style: Gtk.StyleContext,
    ctx: cairo.Context[cairo.Surface],
    x: float,
    y: float,
    radius: float,
    state: str,
    alpha: float,
    *,
    hollow: bool = False,
) -> None:
    _, color = style.lookup_color(_STATUS_COLORS.get(state, "accent_bg_color"))
    ctx.set_source_rgba(color.red, color.green, color.blue, alpha)
    ctx.new_path()
    ctx.arc(x, y, radius, 0, math.tau)
    if hollow or state in {"pending", "cancelled", "verifying"}:
        ctx.set_line_width(2)
        ctx.set_dash((2, 3) if state == "cancelled" else ())
        ctx.stroke()
        ctx.set_dash(())
        if state == "verifying":
            ctx.arc(x, y, radius / 2, 0, math.tau)
            ctx.fill()
    else:
        ctx.fill()


def _legend() -> Gtk.MenuButton:
    button = Gtk.MenuButton(label=translate("graph.legend"))
    popover = Gtk.Popover()
    items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    for state in NodeStatus:
        items.append(_legend_row(f"plan.status.{state.value}", state.value, marker=True))
    items.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
    for kind in _LINK_DASHES:
        items.append(_legend_row(f"graph.link.{kind}", kind, marker=False))
    popover.set_child(items)
    button.set_popover(popover)
    return button


def _legend_row(message: str, value: str, *, marker: bool) -> Gtk.Box:
    row = Gtk.Box(spacing=8)
    sample = Gtk.DrawingArea(content_width=28, content_height=18)

    def draw(area: Gtk.DrawingArea, ctx: cairo.Context[cairo.Surface], width: int, height: int) -> None:
        style = area.get_style_context()
        if marker:
            _draw_marker(style, ctx, width / 2, height / 2, 6, value, 1)
            return
        _, foreground = style.lookup_color("window_fg_color")
        ctx.set_source_rgba(foreground.red, foreground.green, foreground.blue, 0.82)
        ctx.set_line_width(2)
        ctx.set_dash(_LINK_DASHES[value])
        ctx.move_to(1, height / 2)
        ctx.line_to(width - 1, height / 2)
        ctx.stroke()

    sample.set_draw_func(draw)
    row.append(sample)
    row.append(Gtk.Label(label=translate(message), xalign=0))
    return row


def _positions(points: tuple[Point, ...]) -> dict[str, tuple[float, float]]:
    """Distribute groups and leaves over rings, keeping input order stable."""
    groups = tuple(point for point in points if point.group)
    leaves = tuple(point for point in points if not point.group)
    positions: dict[str, tuple[float, float]] = {}
    for batch, rx, ry in ((groups, 130, 86), (leaves, 265, 175)):
        for index, point in enumerate(batch):
            angle = math.tau * index / len(batch) - math.pi / 2
            positions[point.id] = (_MID_X + rx * math.cos(angle), _MID_Y + ry * math.sin(angle))
    if len(points) == 1:
        positions[points[0].id] = (_MID_X, _MID_Y)
    return positions
