"""The plan page shown in place of the terminal: nodes, dependency curves and node details."""

from __future__ import annotations

import math
from collections.abc import Callable
from textwrap import shorten

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gtk, Pango  # noqa: E402

from aconsole.contracts.app import PlanInboxEntry, PlanNodeView, PlanView  # noqa: E402
from aconsole.contracts.common import NodeId, NodeStatus, PlanId, TabId  # noqa: E402
from aconsole.ui.viewmodels.logic.plan_layout import PlanNodePosition, direction_columns, layout_plan, shown_nodes  # noqa: E402
from aconsole.ui.viewmodels.logic.quota_fmt import agent_name  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402

_NODE_WIDTH = 128
_NODE_HEIGHT = 64
_MIN_BEND = 12.0
_PORT_RADIUS = 2.5
_FULL_TURN = 2 * math.pi
_DETAILS_HEIGHT = 96  # room for every detail line, so hovering a node never moves the graph


class PlanGraph(Gtk.Box):
    def __init__(  # noqa: PLR0913 -- explicit callbacks for graph and human decisions
        self,
        plan: PlanView,
        on_close: Callable[[], None],
        on_tab: Callable[[TabId], None] | None = None,
        on_decide: Callable[[NodeId, bool], None] | None = None,
        *,
        on_freeze: Callable[[PlanId], None] | None = None,
        on_review: Callable[[str, str, str], None] | None = None,
        on_repair: Callable[[PlanView], None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("ac-plan-page")
        self.plan: PlanView = plan
        self._on_freeze: Callable[[PlanId], None] | None = on_freeze
        self._on_review: Callable[[str, str, str], None] | None = on_review
        self._on_repair: Callable[[PlanView], None] | None = on_repair
        self._inbox: tuple[PlanInboxEntry, ...] = ()
        self._inbox_key: object = None
        self._review_actions: dict[str, Gtk.Box] = {}
        self._node_width: int = 168 if any(node.level == 0 for node in plan.nodes) else _NODE_WIDTH
        self._node_height: int = 96 if any(node.pipeline for node in plan.nodes) else _NODE_HEIGHT
        self._on_close: Callable[[], None] = on_close
        self._on_tab: Callable[[TabId], None] | None = on_tab
        self._on_decide: Callable[[NodeId, bool], None] | None = on_decide
        self._hovered: NodeId | None = None
        self._selected: NodeId | None = None
        self._pending_decisions: set[NodeId] = set()
        self._nodes: dict[NodeId, PlanNodeView] = {node.id: node for node in shown_nodes(plan.nodes)}
        self._positions: dict[NodeId, PlanNodePosition] = {
            position.node_id: position for position in layout_plan(plan.nodes)
        }
        self.node_buttons: dict[NodeId, Gtk.Button] = {}
        self.append(self._build_heading())
        self.inbox_list: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.inbox_scroll: Gtk.ScrolledWindow = Gtk.ScrolledWindow(
            max_content_height=200, propagate_natural_height=True
        )
        self.inbox_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.inbox_scroll.set_child(self.inbox_list)
        self.inbox_scroll.set_visible(False)
        self.append(self.inbox_scroll)
        self.inbox_error: Gtk.Label = Gtk.Label(xalign=0, wrap=True)
        self.inbox_error.add_css_class("error")
        self.inbox_error.set_visible(False)
        self.append(self.inbox_error)
        self._setup_canvas()
        self.set_inbox(())

    def update(self, plan: PlanView) -> bool:
        """Update state without replacing buttons, scroll or focus when the graph geometry is unchanged."""

        def geometry(node: PlanNodeView) -> tuple[object, ...]:
            return node.id, node.key, node.title, node.layer, node.depends_on, node.level, node.parent

        nodes = {node.id: node for node in shown_nodes(plan.nodes)}
        if plan.id != self.plan.id or tuple(map(geometry, nodes.values())) != tuple(
            map(geometry, self._nodes.values())
        ):
            return False
        for key, node in nodes.items():
            button = self.node_buttons[key]
            button.remove_css_class(f"ac-plan-{self._nodes[key].status.value}")
            button.add_css_class(f"ac-plan-{node.status.value}")
            self._label_node(button, node)
            if node.ready:
                button.add_css_class("ac-plan-ready")
            else:
                button.remove_css_class("ac-plan-ready")
        self.plan = plan
        self._nodes = nodes
        self._title_label.set_text(plan.title)
        self._summary_label.set_text(translate("plan.card.progress").format(done=plan.done, total=plan.total))
        self.phase_label.set_text(translate(f"plan.phase.{plan.phase}"))
        self.freeze_button.set_visible(plan.phase == "draft")
        self.repair_button.set_visible(self._can_repair())
        self.set_inbox(self._inbox)
        self.set_hovered(self._hovered)
        return True

    def _build_heading(self) -> Gtk.Box:
        heading = Gtk.Box(spacing=12)
        heading.add_css_class("ac-plan-heading")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        title = Gtk.Label(label=self.plan.title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self._title_label: Gtk.Label = title
        title.add_css_class("title-2")
        text.append(title)
        progress = translate("plan.card.progress").format(done=self.plan.done, total=self.plan.total)
        summary = Gtk.Label(label=progress, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self._summary_label: Gtk.Label = summary
        summary.add_css_class("ac-summary-detail")
        text.append(summary)
        # On the left: the top right corner may hold the floating window buttons.
        self.close_button: Gtk.Button = Gtk.Button(label=translate("plan.back"), valign=Gtk.Align.CENTER)
        self.close_button.add_css_class("ac-plan-back")
        _ = self.close_button.connect("clicked", self._close)
        heading.append(self.close_button)
        heading.append(text)
        self.phase_label: Gtk.Label = Gtk.Label(label=translate(f"plan.phase.{self.plan.phase}"))
        heading.append(self.phase_label)
        self.freeze_button: Gtk.Button = Gtk.Button(label=translate("plan.freeze"), valign=Gtk.Align.CENTER)
        self.freeze_button.set_visible(self.plan.phase == "draft")
        self.freeze_button.set_sensitive(self._on_freeze is not None)
        _ = self.freeze_button.connect("clicked", self._freeze)
        heading.append(self.freeze_button)
        self.repair_button: Gtk.Button = Gtk.Button(label=translate("plan.repair"), valign=Gtk.Align.CENTER)
        self.repair_button.set_visible(self._can_repair())
        _ = self.repair_button.connect("clicked", self._repair)
        heading.insert_child_after(self.repair_button, self.close_button)
        return heading

    def _can_repair(self) -> bool:
        return (
            self._on_repair is not None
            and self.plan.phase == "frozen"
            and any(node.level == 0 for node in self.plan.nodes)
            and all(node.status in {NodeStatus.PENDING, NodeStatus.BLOCKED}
                    and node.tab_id is None and node.attempt_no == 0 for node in self.plan.nodes)
        )

    def _repair(self, _button: Gtk.Button) -> None:
        if self._can_repair() and self._on_repair is not None:
            self._on_repair(self.plan)

    def _freeze(self, _button: Gtk.Button) -> None:
        if self._on_freeze is not None and self.freeze_button.get_sensitive() and self.plan.phase == "draft":
            self.freeze_button.set_sensitive(False)
            self._on_freeze(self.plan.id)

    def freeze_finished(self) -> None:
        self.freeze_button.set_sensitive(self._on_freeze is not None)

    def set_inbox(self, entries: tuple[PlanInboxEntry, ...]) -> None:
        reviewing = tuple(node for node in self._nodes.values() if node.status is NodeStatus.VERIFYING)
        key = (entries, tuple(node.id for node in reviewing))
        if key == self._inbox_key:
            return
        self._inbox_key, self._inbox = key, entries
        self._review_actions.clear()
        while (child := self.inbox_list.get_first_child()) is not None:
            self.inbox_list.remove(child)
        self.inbox_scroll.set_visible(bool(entries or reviewing))
        title = Gtk.Label(label=translate("plan.attention"), xalign=0)
        title.add_css_class("heading")
        self.inbox_list.append(title)
        for node in reviewing:
            button = Gtk.Button(label=f"{node.key} · {node.title}")
            _ = button.connect("clicked", self._node_clicked, node.id)
            self.inbox_list.append(button)
        for entry in entries:
            self._inbox_row(entry)

    def set_inbox_error(self, error: str | None) -> None:
        self.inbox_error.set_text(error or "")
        self.inbox_error.set_visible(error is not None)

    def review_finished(self, request_id: str) -> None:
        actions = self._review_actions.get(request_id)
        if actions is not None:
            actions.set_sensitive(True)

    def _inbox_row(self, entry: PlanInboxEntry) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        row.add_css_class("ac-plan-details")
        label = Gtk.Label(label=entry.body or entry.kind, xalign=0, wrap=True, selectable=True)
        row.append(label)
        note = Gtk.Entry(placeholder_text=translate("plan.review_note"), hexpand=True)
        row.append(note)
        actions = Gtk.Box(spacing=6)
        self._review_actions[entry.id] = actions
        choices = (("apply", "plan.apply"),) if entry.kind in {"add", "split"} else (("unblock", "plan.unblock"),)
        cancel = (("cancel", "plan.cancel"),) if entry.node_id is not None else ()
        for action, message in (*choices, *cancel, ("reject", "plan.reject")):
            button = Gtk.Button(label=translate(message))
            button.set_sensitive(self._on_review is not None)
            _ = button.connect("clicked", self._review, entry.id, action, note)
            actions.append(button)
        row.append(actions)
        self.inbox_list.append(row)

    def _review(self, _button: Gtk.Button, request_id: str, action: str, note: Gtk.Entry) -> None:
        if not self._review_actions[request_id].get_sensitive():
            return
        if action == "unblock" and not note.get_text().strip():
            _ = note.grab_focus()
            return
        if self._on_review is not None:
            self._review_actions[request_id].set_sensitive(False)
            self._on_review(request_id, action, note.get_text())

    def _setup_canvas(self) -> None:
        width = max((position.x for position in self._positions.values()), default=0) + self._node_width + 16
        height = max((position.y for position in self._positions.values()), default=0) + self._node_height + 12
        overlay = Gtk.Overlay(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.drawing: Gtk.DrawingArea = Gtk.DrawingArea(content_width=width, content_height=height)
        self.drawing.set_draw_func(self._draw)
        overlay.set_child(self.drawing)
        fixed = Gtk.Fixed()
        overlay.add_overlay(fixed)
        if any(node.level == 0 for node in self.plan.nodes):
            for column, (_identifier, title) in enumerate(direction_columns(self.plan.nodes)):
                label = Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
                label.add_css_class("heading")
                label.set_size_request(self._node_width, 30)
                label.set_tooltip_text(title)
                fixed.put(label, column * 196 + 12, 4)
        for node_id, node in self._nodes.items():
            button = Gtk.Button()
            label = Gtk.Label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=16, justify=Gtk.Justification.CENTER)
            button.set_child(label)
            self._label_node(button, node)
            button.set_size_request(self._node_width, self._node_height)
            button.add_css_class("ac-plan-node")
            button.add_css_class(f"ac-plan-{node.status.value}")
            self._label_node(button, node)
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
        # Review stays beside the heading, above the window's bottom approval overlay.
        self.insert_child_after(self._build_details(), self.get_first_child())

    @staticmethod
    def _label_node(button: Gtk.Button, node: PlanNodeView) -> None:
        status = translate(f"plan.status.{node.status.value}")
        label = button.get_child()
        if isinstance(label, Gtk.Label):
            key = GLib.markup_escape_text(node.key)
            title = GLib.markup_escape_text(node.title)
            extra = _attempt_details(node)
            extra_text = (
                "\n" + "\n".join(shorten(line.splitlines()[0], width=32, placeholder="…") for line in extra)
                if extra
                else ""
            )
            label.set_markup(
                f"<small>{key}</small>\n<b>{title}</b>\n<small>{GLib.markup_escape_text(status + extra_text)}</small>"
            )
        name = f"{node.key}: {node.title} — {status}"
        button.set_tooltip_text(name + ("\n" + node.last_verdict if node.last_verdict else ""))
        button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [GObject.Value(GObject.TYPE_STRING, name)],
        )

    def _build_details(self) -> Gtk.Box:
        self.details_label: Gtk.Label = Gtk.Label(
            label=translate("plan.select_node"),
            xalign=0,
            yalign=0,
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            selectable=True,
        )
        self.details_label.add_css_class("ac-plan-details-text")
        self.details_label.set_hexpand(True)
        # A fixed height: hovering a node never moves the graph, and a long report scrolls by itself.
        details_scroll = Gtk.ScrolledWindow(hexpand=True, min_content_height=_DETAILS_HEIGHT)
        details_scroll.set_max_content_height(_DETAILS_HEIGHT)
        details_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        details_scroll.set_child(self.details_label)
        details = Gtk.Box(spacing=16)
        details.add_css_class("ac-plan-details")
        details.append(details_scroll)
        self.accept_button: Gtk.Button = Gtk.Button(label=translate("plan.accept"), valign=Gtk.Align.START)
        self.accept_button.add_css_class("suggested-action")
        self.accept_button.set_visible(False)
        _ = self.accept_button.connect("clicked", self._decide, True)  # noqa: FBT003 -- GObject passes it positionally
        details.append(self.accept_button)
        self.send_back_button: Gtk.Button = Gtk.Button(label=translate("plan.send_back"), valign=Gtk.Align.START)
        self.send_back_button.add_css_class("ac-plan-back")
        self.send_back_button.set_visible(False)
        _ = self.send_back_button.connect("clicked", self._decide, False)  # noqa: FBT003 -- GObject passes it positionally
        details.append(self.send_back_button)
        self.tab_button: Gtk.Button = Gtk.Button(label=translate("plan.go_tab"), valign=Gtk.Align.START)
        self.tab_button.add_css_class("ac-plan-back")
        self.tab_button.set_sensitive(False)
        _ = self.tab_button.connect("clicked", self._go_tab)
        details.append(self.tab_button)
        return details

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

    def _decide(self, _button: Gtk.Button, accept: bool) -> None:  # noqa: FBT001 -- a GObject signal argument
        node = self._nodes.get(self._selected) if self._selected is not None else None
        if (
            node is not None
            and node.status is NodeStatus.VERIFYING
            and node.id not in self._pending_decisions
            and self._on_decide is not None
        ):
            self._pending_decisions.add(node.id)
            self.accept_button.set_sensitive(False)
            self.send_back_button.set_sensitive(False)
            self._on_decide(node.id, accept)

    def decision_failed(self) -> None:
        """The core refused: the node is as it was, so the buttons work again."""
        if self._selected is not None:
            self.decision_finished(self._selected)

    def decision_finished(self, node_id: NodeId) -> None:
        self._pending_decisions.discard(node_id)
        self._sync_decision_buttons()

    def _sync_decision_buttons(self) -> None:
        enabled = self._selected not in self._pending_decisions
        self.accept_button.set_sensitive(enabled)
        self.send_back_button.set_sensitive(enabled)

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
        deciding = selected is not None and selected.status is NodeStatus.VERIFYING and self._on_decide is not None
        self.accept_button.set_visible(deciding)
        self.send_back_button.set_visible(deciding)
        self._sync_decision_buttons()
        if selected is None:
            self.details_label.set_label(translate("plan.select_node"))
        else:
            node = selected
            parents = ", ".join(self._nodes[key].key for key in node.depends_on if key in self._nodes)
            children = ", ".join(item.key for item in self._nodes.values() if node.id in item.depends_on)
            text = [f"{node.key} {node.title}", translate(f"plan.status.{node.status.value}")]
            if node.owner_agent is not None:
                text.append(agent_name(node.owner_agent.value))
            text.extend(
                (
                    translate("plan.depends").format(nodes=parents or "—"),
                    translate("plan.blocks").format(nodes=children or "—"),
                )
            )
            if node.lease_expires_at is not None:
                text.append(translate("plan.lease").format(time=node.lease_expires_at.astimezone().strftime("%H:%M")))
            text.extend(_attempt_details(node))
            if node.evidence:
                text.append(translate("plan.evidence").format(text=node.evidence))
            head, *rest = text
            self.details_label.set_markup(
                "\n".join(
                    (f"<b>{GLib.markup_escape_text(head)}</b>", *(GLib.markup_escape_text(line) for line in rest))
                )
            )
        self.drawing.queue_draw()

    def _draw(self, _area: Gtk.DrawingArea, context: cairo.Context[cairo.Surface], _width: int, _height: int) -> None:
        style = self.get_style_context()
        _found, line = style.lookup_color("window_fg_color")
        _found, accent = style.lookup_color("accent_bg_color")
        _found, done = style.lookup_color("success_color")
        middle_y = self._node_height / 2
        context.set_line_cap(cairo.LineCap.ROUND)
        context.set_line_join(cairo.LineJoin.ROUND)
        ports: list[tuple[float, float, Gdk.RGBA, float]] = []
        for node in self._nodes.values():
            target = self._positions[node.id]
            for parent_id in node.depends_on:
                source = self._positions.get(parent_id)
                parent = self._nodes.get(parent_id)
                if source is None or parent is None:
                    continue
                active = self._hovered is not None and self._hovered in (node.id, parent_id)
                # A finished dependency no longer holds anything up: its edge takes the done colour.
                color = accent if active else done if parent.status.value == "done" else line
                if active:
                    alpha = 1.0
                elif self._hovered:
                    alpha = 0.08
                else:
                    alpha = 0.45 if color is done else 0.28
                context.set_source_rgba(color.red, color.green, color.blue, alpha)
                context.set_line_width(2.25 if active else 1.5)
                start_x, end_x = source.x + self._node_width, target.x
                start_y, end_y = source.y + middle_y, target.y + middle_y
                bend = max((end_x - start_x) / 2, _MIN_BEND)
                context.move_to(start_x, start_y)
                context.curve_to(start_x + bend, start_y, end_x - bend, end_y, end_x, end_y)
                context.stroke()
                ports.append((end_x, end_y, color, alpha))
        # A small dot where an edge enters a node says which way the dependency runs.
        for x, y, color, alpha in ports:
            context.set_source_rgba(color.red, color.green, color.blue, min(1.0, alpha * 1.6))
            context.arc(x, y, _PORT_RADIUS, 0, _FULL_TURN)
            context.fill()


def _attempt_details(node: PlanNodeView) -> list[str]:
    lines = (
        [translate("plan.attempt").format(number=node.attempt_no, maximum=node.max_attempts)] if node.pipeline else []
    )
    if node.last_verdict:
        lines.append(node.last_verdict)
    return lines
