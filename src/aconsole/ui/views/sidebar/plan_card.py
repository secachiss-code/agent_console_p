"""The plan half of the shared sidebar summary."""

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango  # noqa: E402

from aconsole.contracts.app import PlanView  # noqa: E402
from aconsole.contracts.common import PlanId  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402


class PlanCard(Gtk.Button):
    def __init__(self, on_activate: Callable[[PlanId], None]) -> None:
        super().__init__()
        self.add_css_class("flat")
        self.add_css_class("ac-summary-section")
        self.plan_id: PlanId | None = None
        self._on_activate: Callable[[PlanId], None] = on_activate
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        heading = Gtk.Box(spacing=8)
        label = Gtk.Label(label=translate("plan.card.title").upper(), xalign=0)
        label.add_css_class("caption-heading")
        heading.append(label)
        self.title_label: Gtk.Label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        heading.append(self.title_label)
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
        self.plan_id = plan.id if plan is not None else None
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
            self._on_activate(self.plan_id)


def create_plan_placeholder(plan: PlanView) -> Adw.StatusPage:
    return Adw.StatusPage(title=plan.title, description=f"{plan.goal}\n\n{translate('plan.page.soon')}")
