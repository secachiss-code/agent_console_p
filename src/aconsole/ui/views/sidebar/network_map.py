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

from aconsole.ui.i18n import _ as translate  # noqa: E402

_WIDTH = 680
_HEIGHT = 460
_MID_X = _WIDTH / 2
_MID_Y = _HEIGHT / 2
_RADIUS = 11
_CLICK_RADIUS = 28
_LABELS = 45


@dataclass(frozen=True, slots=True)
class Point:
    id: str
    label: str
    group: bool = False
    state: str = ""


@dataclass(frozen=True, slots=True)
class Link:
    source: str
    target: str
    kind: str


class NetworkMap(Gtk.Box):
    def __init__(self, on_select: Callable[[str], None]) -> None:
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
        self._choices: Gtk.DropDown = Gtk.DropDown.new_from_strings(())
        self._choices.set_enable_search(True)
        self._choices.set_hexpand(True)
        self._choices.set_tooltip_text(translate("graph.select"))
        _ = self._choices.connect("notify::selected", self._choose)
        toolbar.append(self._choices)
        for value, icon, label in (
            (-1, "zoom-out-symbolic", "graph.zoom_out"), (1, "zoom-in-symbolic", "graph.zoom_in"),
        ):
            button = Gtk.Button(icon_name=icon, tooltip_text=translate(label))
            _ = button.connect("clicked", self._scale, value)
            toolbar.append(button)
        self.append(toolbar)
        self._area: Gtk.DrawingArea = Gtk.DrawingArea(content_width=_WIDTH, content_height=_HEIGHT)
        self._area.set_halign(Gtk.Align.CENTER)
        self._area.set_valign(Gtk.Align.CENTER)
        self._area.set_draw_func(self._draw)
        click = Gtk.GestureClick()
        _ = click.connect("released", self._hit)
        self._area.add_controller(click)
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroll.set_child(self._area)
        self.append(scroll)

    def set_graph(self, points: tuple[Point, ...], links: tuple[Link, ...]) -> None:
        if points == self._points and links == self._links:
            return
        old_ids = tuple(point.id for point in self._points)
        self._points, self._links = points, links
        if old_ids != tuple(point.id for point in points):
            self._positions = _positions(points)
            self._updating = True
            self._choices.set_model(Gtk.StringList.new(tuple(point.label for point in points)))
            self._choices.set_selected(Gtk.INVALID_LIST_POSITION)
            self._updating = False
        self.highlight(self._selected)

    def highlight(self, node_id: str | None) -> None:
        self._selected = node_id
        self._neighbors = {
            link.target if link.source == node_id else link.source
            for link in self._links if node_id in (link.source, link.target)
        }
        self._area.queue_draw()

    def _scale(self, _button: Gtk.Button, direction: int) -> None:
        self._zoom = max(0.65, min(2.5, self._zoom + 0.2 * direction))
        self._area.set_content_width(round(_WIDTH * self._zoom))
        self._area.set_content_height(round(_HEIGHT * self._zoom))
        self._area.queue_draw()

    def _choose(self, dropdown: Gtk.DropDown, _param: GObject.ParamSpec) -> None:
        index = dropdown.get_selected()
        if not self._updating and index < len(self._points):
            self._on_select(self._points[index].id)

    def _hit(self, _gesture: Gtk.GestureClick, _presses: int, x: float, y: float) -> None:
        distances = ((math.hypot(cx - x / self._zoom, cy - y / self._zoom), identifier)
                     for identifier, (cx, cy) in self._positions.items())
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
            ctx.set_dash((4, 3) if link.kind in {"related", "decomposes"} else ())
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
        status_colors = {"done": "success_color", "failed": "error_color", "blocked": "warning_color"}
        _, color = style.lookup_color(status_colors.get(point.state, "accent_bg_color"))
        x, y = self._positions[point.id]
        radius = _RADIUS + (6 if point.group else 0)
        alpha = 1 if selected else 0.78 if self._selected is None or point.id in self._neighbors else 0.4
        ctx.set_source_rgba(color.red, color.green, color.blue, alpha)
        ctx.new_path()
        ctx.arc(x, y, radius, 0, math.tau)
        if selected or not point.group:
            ctx.fill()
        else:
            ctx.set_line_width(3)
            ctx.stroke()
        if label:
            text = shorten(point.label, width=24, placeholder="…")
            ctx.set_source_rgba(foreground.red, foreground.green, foreground.blue, alpha)
            ctx.set_font_size(12)
            ctx.move_to(x - ctx.text_extents(text).width / 2, y + radius + 15)
            ctx.show_text(text)


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
