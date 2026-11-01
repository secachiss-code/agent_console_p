"""Native plan-node preview with dependency arrows and node details."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from aconsole.contracts.app import PlanNodeView, PlanView  # noqa: E402
from aconsole.contracts.common import NodeId, PlanId, TabId  # noqa: E402
from aconsole.ui.viewmodels.logic.plan_layout import PlanNodePosition, layout_plan  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402

if TYPE_CHECKING:
    import cairo


class PlanGraph(Gtk.Box):
    def __init__(
        self, plan: PlanView, on_open: Callable[[PlanId], None], on_tab: Callable[[TabId], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.add_css_class("ac-plan-preview")
        self.plan: PlanView = plan
        self._on_open: Callable[[PlanId], None] = on_open
        self._on_tab: Callable[[TabId], None] | None = on_tab
        self._hovered: NodeId | None = None
        self._selected: NodeId | None = None
        self._nodes: dict[NodeId, PlanNodeView] = {node.id: node for node in plan.nodes[:30]}
        self._positions: dict[NodeId, PlanNodePosition] = {
            position.node_id: position for position in layout_plan(tuple(self._nodes.values()))
        }
        self.node_buttons: dict[NodeId, Gtk.Button] = {}
        heading = Gtk.Label(label=plan.title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        heading.add_css_class("heading")
        self.append(heading)
        self._setup_canvas()
        button = Gtk.Button(label=translate("plan.open"))
        _ = button.connect("clicked", self._open)
        self.append(button)

    def _setup_canvas(self) -> None:
        width = max((position.x for position in self._positions.values()), default=0) + 144
        height = max((position.y for position in self._positions.values()), default=0) + 56
        overlay = Gtk.Overlay()
        self.drawing: Gtk.DrawingArea = Gtk.DrawingArea(content_width=width, content_height=height)
        self.drawing.set_draw_func(self._draw)
        overlay.set_child(self.drawing)
        fixed = Gtk.Fixed()
        overlay.add_overlay(fixed)
        for node_id, node in self._nodes.items():
            button = Gtk.Button()
            label = Gtk.Label(label=f"{node.key}\n{node.title}", ellipsize=Pango.EllipsizeMode.END, max_width_chars=16)
            button.set_child(label)
            button.set_size_request(128, 44)
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
        scroll = Gtk.ScrolledWindow(
            min_content_width=320, max_content_width=440, min_content_height=160, max_content_height=320,
            propagate_natural_width=True, propagate_natural_height=True,
        )
        scroll.set_child(overlay)
        body = Gtk.Box(spacing=12)
        body.append(scroll)
        self.details_label: Gtk.Label = Gtk.Label(label=translate("plan.select_node"), xalign=0, yalign=0, wrap=True)
        self.details_label.set_width_chars(20)
        self.details_label.set_max_width_chars(24)
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        details.append(self.details_label)
        self.tab_button: Gtk.Button = Gtk.Button(label=translate("plan.go_tab"))
        self.tab_button.set_sensitive(False)
        _ = self.tab_button.connect("clicked", self._go_tab)
        details.append(self.tab_button)
        body.append(details)
        self.append(body)

    def _open(self, _button: Gtk.Button) -> None:
        self._on_open(self.plan.id)

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
            self.details_label.set_label("\n".join(text))
        self.drawing.queue_draw()

    def _draw(self, _area: Gtk.DrawingArea, context: cairo.Context[cairo.Surface], _width: int, _height: int) -> None:
        _found, color = self.get_style_context().lookup_color("accent_bg_color")
        for node in self._nodes.values():
            target = self._positions[node.id]
            for parent_id in node.depends_on:
                source = self._positions.get(parent_id)
                if source is None:
                    continue
                active = self._hovered is not None and self._hovered in (node.id, parent_id)
                opacity = 1 if self._hovered is None or active else 0.45
                context.set_source_rgba(color.red, color.green, color.blue, color.alpha * opacity)
                context.set_line_width(2.5 if active else 1.5)
                start_x, end_x = source.x + 128, target.x
                middle = (start_x + end_x) / 2
                context.move_to(start_x, source.y + 22)
                context.line_to(middle, source.y + 22)
                context.line_to(middle, target.y + 22)
                context.line_to(end_x, target.y + 22)
                context.stroke()
                context.move_to(end_x, target.y + 22)
                context.line_to(end_x - 6, target.y + 18)
                context.line_to(end_x - 6, target.y + 26)
                context.close_path()
                context.fill()
