"""The plan page shown in place of the terminal: nodes, dependency curves and node details."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from aconsole.contracts.app import PlanNodeView, PlanView  # noqa: E402
from aconsole.contracts.common import NodeId, TabId  # noqa: E402
from aconsole.ui.viewmodels.logic.plan_layout import PlanNodePosition, layout_plan  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402

if TYPE_CHECKING:
    import cairo


_NODE_WIDTH = 128
_NODE_HEIGHT = 44
_DETAILS_HEIGHT = 96  # room for every detail line, so hovering a node never moves the graph


class PlanGraph(Gtk.Box):
    def __init__(
        self, plan: PlanView, on_close: Callable[[], None], on_tab: Callable[[TabId], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("ac-plan-page")
        self.plan: PlanView = plan
        self._on_close: Callable[[], None] = on_close
        self._on_tab: Callable[[TabId], None] | None = on_tab
        self._hovered: NodeId | None = None
        self._selected: NodeId | None = None
        self._nodes: dict[NodeId, PlanNodeView] = {node.id: node for node in plan.nodes[:30]}
        self._positions: dict[NodeId, PlanNodePosition] = {
            position.node_id: position for position in layout_plan(tuple(self._nodes.values()))
        }
        self.node_buttons: dict[NodeId, Gtk.Button] = {}
        self.append(self._build_heading())
        self._setup_canvas()

    def _build_heading(self) -> Gtk.Box:
        heading = Gtk.Box(spacing=12)
        heading.add_css_class("ac-plan-heading")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        title = Gtk.Label(label=self.plan.title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class("title-2")
        text.append(title)
        progress = translate("plan.card.progress").format(done=self.plan.done, total=self.plan.total)
        summary = Gtk.Label(label=progress, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        summary.add_css_class("ac-summary-detail")
        text.append(summary)
        heading.append(text)
        self.close_button: Gtk.Button = Gtk.Button(label=translate("plan.back"), valign=Gtk.Align.CENTER)
        self.close_button.add_css_class("pill")
        self.close_button.add_css_class("ac-plan-back")
        _ = self.close_button.connect("clicked", self._close)
        heading.append(self.close_button)
        return heading

    def _setup_canvas(self) -> None:
        width = max((position.x for position in self._positions.values()), default=0) + _NODE_WIDTH + 16
        height = max((position.y for position in self._positions.values()), default=0) + _NODE_HEIGHT + 12
        overlay = Gtk.Overlay(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.drawing: Gtk.DrawingArea = Gtk.DrawingArea(content_width=width, content_height=height)
        self.drawing.set_draw_func(self._draw)
        overlay.set_child(self.drawing)
        fixed = Gtk.Fixed()
        overlay.add_overlay(fixed)
        for node_id, node in self._nodes.items():
            button = Gtk.Button()
            label = Gtk.Label(label=f"{node.key}\n{node.title}", ellipsize=Pango.EllipsizeMode.END, max_width_chars=16)
            button.set_child(label)
            button.set_size_request(_NODE_WIDTH, _NODE_HEIGHT)
            button.add_css_class("ac-plan-node")
            button.add_css_class(f"ac-plan-{node.status.value}")
            if node.ready:
                button.add_css_class("ac-plan-ready")
            _ = button.connect("clicked", self._node_clicked, node_id)
            motion = Gtk.EventControllerMotion()
            _ = motion.connect("enter", self._node_enter, node_id)
            _ = motion.connect("leave", self._node_leave)
            button.add_controller(motion)
            focus = Gtk.EventControllerFocus()
            _ = focus.connect("enter", self._node_focus, node_id)
            button.add_controller(focus)
            self.node_buttons[node_id] = button
            position = self._positions[node_id]
            fixed.put(button, position.x, position.y)
        scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroll.set_child(overlay)
        self.append(scroll)
        self.details_label: Gtk.Label = Gtk.Label(label=translate("plan.select_node"), xalign=0, yalign=0, wrap=True)
        self.details_label.add_css_class("ac-plan-details-text")
        self.details_label.set_hexpand(True)
        self.details_label.set_size_request(-1, _DETAILS_HEIGHT)
        details = Gtk.Box(spacing=16)
        details.add_css_class("ac-plan-details")
        details.append(self.details_label)
        self.tab_button: Gtk.Button = Gtk.Button(label=translate("plan.go_tab"), valign=Gtk.Align.START)
        self.tab_button.add_css_class("pill")
        self.tab_button.add_css_class("ac-plan-back")
        self.tab_button.set_sensitive(False)
        _ = self.tab_button.connect("clicked", self._go_tab)
        details.append(self.tab_button)
        self.append(details)

    def _close(self, _button: Gtk.Button) -> None:
        self._on_close()

    def _node_clicked(self, _button: Gtk.Button, node_id: NodeId) -> None:
        self.set_hovered(node_id)

    def _node_enter(self, _motion: Gtk.EventControllerMotion, _x: float, _y: float, node_id: NodeId) -> None:
        self.set_hovered(node_id)

    def _node_focus(self, _focus: Gtk.EventControllerFocus, node_id: NodeId) -> None:
        self.set_hovered(node_id)

    def _node_leave(self, _motion: Gtk.EventControllerMotion) -> None:
        self.set_hovered(None)

    def _go_tab(self, _button: Gtk.Button) -> None:
        node = self._nodes.get(self._selected) if self._selected is not None else None
        if node is not None and node.tab_id is not None and self._on_tab is not None:
            self._on_tab(node.tab_id)

    def set_hovered(self, node_id: NodeId | None) -> None:
        node = self._nodes.get(node_id) if node_id is not None else None
        self._hovered = node.id if node is not None else None
        if node is not None:
            self._selected = node.id
        neighbors: set[NodeId] = set(node.depends_on) if node is not None else set()
        if node is not None:
            neighbors.add(node.id)
            neighbors.update(item.id for item in self._nodes.values() if node.id in item.depends_on)
        for key, button in self.node_buttons.items():
            button.set_opacity(0.45 if node is not None and key not in neighbors else 1)
            if node is not None and key not in neighbors:
                button.add_css_class("ac-plan-muted")
            else:
                button.remove_css_class("ac-plan-muted")
        selected = self._nodes.get(self._selected) if self._selected is not None else None
        self.tab_button.set_sensitive(selected is not None and selected.tab_id is not None and self._on_tab is not None)
        if selected is None:
            self.details_label.set_label(translate("plan.select_node"))
        else:
            node = selected
            parents = ", ".join(self._nodes[key].key for key in node.depends_on if key in self._nodes)
            children = ", ".join(item.key for item in self._nodes.values() if node.id in item.depends_on)
            text = [f"{node.key} {node.title}", translate(f"plan.status.{node.status.value}")]
            if node.owner_agent is not None:
                text.append(node.owner_agent.value.capitalize())
            text.extend((translate("plan.depends").format(nodes=parents or "—"),
                         translate("plan.blocks").format(nodes=children or "—")))
            if node.lease_expires_at is not None:
                text.append(translate("plan.lease").format(time=node.lease_expires_at.astimezone().strftime("%H:%M")))
            head, *rest = text
            self.details_label.set_markup("\n".join((f"<b>{GLib.markup_escape_text(head)}</b>",
                                                     *(GLib.markup_escape_text(line) for line in rest))))
        self.drawing.queue_draw()

    def _draw(self, _area: Gtk.DrawingArea, context: cairo.Context[cairo.Surface], _width: int, _height: int) -> None:
        style = self.get_style_context()
        _found, line = style.lookup_color("window_fg_color")
        _found, accent = style.lookup_color("accent_bg_color")
        middle_y = _NODE_HEIGHT / 2
        for node in self._nodes.values():
            target = self._positions[node.id]
            for parent_id in node.depends_on:
                source = self._positions.get(parent_id)
                if source is None:
                    continue
                active = self._hovered is not None and self._hovered in (node.id, parent_id)
                if active:
                    context.set_source_rgba(accent.red, accent.green, accent.blue, 1)
                else:
                    context.set_source_rgba(line.red, line.green, line.blue, 0.08 if self._hovered else 0.22)
                context.set_line_width(2 if active else 1.25)
                start_x, end_x = source.x + _NODE_WIDTH, target.x
                start_y, end_y = source.y + middle_y, target.y + middle_y
                bend = (end_x - start_x) / 2
                context.move_to(start_x, start_y)
                context.curve_to(start_x + bend, start_y, end_x - bend, end_y, end_x, end_y)
                context.stroke()
