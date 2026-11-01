"""The plan half of the shared sidebar summary."""

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from aconsole.contracts.app import PlanView  # noqa: E402
from aconsole.contracts.common import PlanId, TabId  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402
from aconsole.ui.views.sidebar.plan_graph import PlanGraph  # noqa: E402


class PlanCard(Gtk.Button):
    def __init__(self, on_activate: Callable[[PlanId], None], on_tab: Callable[[TabId], None] | None = None) -> None:
        super().__init__()
        self.add_css_class("flat")
        self.add_css_class("ac-summary-section")
        self.plan_id: PlanId | None = None
        self._on_activate: Callable[[PlanId], None] = on_activate
        self._on_tab: Callable[[TabId], None] | None = on_tab
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        heading = Gtk.Box(spacing=8)
        label = Gtk.Label(label=translate("plan.card.title").upper(), xalign=0)
        label.add_css_class("caption-heading")
        heading.append(label)
        self.title_label: Gtk.Label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        heading.append(self.title_label)
        self._setup_graph(heading)
        box.append(heading)
        self.progress_label: Gtk.Label = Gtk.Label(xalign=0)
        self.progress_label.add_css_class("numeric")
        box.append(self.progress_label)
        self.progress_bar: Gtk.ProgressBar = Gtk.ProgressBar()
        box.append(self.progress_bar)
        self.detail_label: Gtk.Label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.detail_label.add_css_class("ac-summary-detail")
        box.append(self.detail_label)
        self.set_child(box)
        _ = self.connect("clicked", self._activate)
        self.set_plan(None)

    def set_plan(self, plan: PlanView | None) -> None:
        if plan == self._plan and self.title_label.get_label():
            return
        self._plan = plan
        self.plan_id = plan.id if plan is not None else None
        self.graph = PlanGraph(plan, self._open_from_graph, self._jump_to_tab) if plan is not None else None
        self.graph_popover.set_child(self.graph)
        if plan is None:
            self.hide_graph()
        self.title_label.set_label(plan.title if plan is not None else translate("plan.card.unknown"))
        self.progress_label.set_visible(plan is not None)
        self.progress_bar.set_visible(plan is not None)
        if plan is None:
            self.progress_label.set_label("")
            self.progress_bar.set_fraction(0)
            self.detail_label.set_label(translate("plan.empty"))
        else:
            self.progress_label.set_label(translate("plan.card.progress").format(done=plan.done, total=plan.total))
            self.progress_bar.set_fraction(min(1, max(0, plan.done / plan.total)) if plan.total else 0)
            ready = ", ".join(node.key for node in plan.nodes if node.ready)
            self.detail_label.set_label(translate("plan.ready").format(nodes=ready) if ready else plan.goal)
        self.set_tooltip_text(self.detail_label.get_label())

    def _activate(self, _button: Gtk.Button) -> None:
        if self.plan_id is not None:
            self.hide_graph()
            self._on_activate(self.plan_id)

    def _setup_graph(self, heading: Gtk.Box) -> None:
        self._plan: PlanView | None = None
        self.graph: PlanGraph | None = None
        self._show_source: int | None = None
        self._hide_source: int | None = None
        self._hover_zones: set[str] = set()
        self.graph_popover: Gtk.Popover = Gtk.Popover()
        self.graph_popover.set_position(Gtk.PositionType.RIGHT)
        anchor = Gtk.MenuButton()
        anchor.set_direction(Gtk.ArrowType.NONE)
        anchor.set_always_show_arrow(False)
        anchor.set_child(Gtk.Box())
        anchor.set_has_frame(False)
        anchor.set_focusable(False)
        anchor.add_css_class("ac-popover-anchor")
        anchor.set_popover(self.graph_popover)
        heading.append(anchor)
        self._track_preview(self, "card")
        self._track_preview(self.graph_popover, "popover")
        _ = self.connect("unmap", self._preview_unmapped)

    def _track_preview(self, widget: Gtk.Widget, zone: str) -> None:
        motion = Gtk.EventControllerMotion()
        _ = motion.connect("enter", self._preview_enter, zone)
        _ = motion.connect("leave", self._preview_leave, zone)
        widget.add_controller(motion)
        focus = Gtk.EventControllerFocus()
        _ = focus.connect("enter", self._preview_focus_enter, zone)
        _ = focus.connect("leave", self._preview_focus_leave, zone)
        widget.add_controller(focus)

    def _preview_enter(self, _motion: Gtk.EventControllerMotion, _x: float, _y: float, zone: str) -> None:
        self._hover_zones.add(zone)
        self._cancel_hide()
        if self._show_source is None and not self.graph_popover.get_visible():
            self._show_source = GLib.timeout_add(400, self._open_preview)

    def _preview_leave(self, _motion: Gtk.EventControllerMotion, zone: str) -> None:
        self._hover_zones.discard(zone)
        self._schedule_hide()

    def _preview_focus_enter(self, _focus: Gtk.EventControllerFocus, zone: str) -> None:
        self._hover_zones.add(f"focus-{zone}")
        self._cancel_hide()
        self.show_graph()

    def _preview_focus_leave(self, _focus: Gtk.EventControllerFocus, zone: str) -> None:
        self._hover_zones.discard(f"focus-{zone}")
        self._schedule_hide()

    def _open_preview(self) -> bool:
        self._show_source = None
        if self._hover_zones:
            self.show_graph()
        return GLib.SOURCE_REMOVE

    def _cancel_hide(self) -> None:
        if self._hide_source is not None:
            _ = GLib.source_remove(self._hide_source)
            self._hide_source = None

    def _schedule_hide(self) -> None:
        self._cancel_hide()
        self._hide_source = GLib.timeout_add(150, self._hide_if_outside)

    def _hide_if_outside(self) -> bool:
        self._hide_source = None
        if not self._hover_zones:
            self.hide_graph()
        return GLib.SOURCE_REMOVE

    def show_graph(self) -> None:
        if self.graph is not None:
            self.graph_popover.popup()

    def hide_graph(self) -> None:
        if self._show_source is not None:
            _ = GLib.source_remove(self._show_source)
            self._show_source = None
        self._cancel_hide()
        self.graph_popover.popdown()

    def _preview_unmapped(self, _widget: Gtk.Widget) -> None:
        self._hover_zones.clear()
        self.hide_graph()

    def _open_from_graph(self, plan_id: PlanId) -> None:
        self.hide_graph()
        self._on_activate(plan_id)

    def _jump_to_tab(self, tab_id: TabId) -> None:
        self.hide_graph()
        if self._on_tab is not None:
            self._on_tab(tab_id)


def create_plan_placeholder(plan: PlanView) -> Adw.StatusPage:
    return Adw.StatusPage(title=plan.title, description=f"{plan.goal}\n\n{translate('plan.page.soon')}")
