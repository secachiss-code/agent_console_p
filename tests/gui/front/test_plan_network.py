"""The primary plan graph preserves its viewport when a status changes."""

# pyright: reportPrivateUsage=false

import gi
from msgspec.structs import replace

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from aconsole.contracts.common import NodeStatus  # noqa: E402
from aconsole.ui.views.sidebar.plan_graph import PlanGraph  # noqa: E402
from tests.gui.front.test_b4a_plan import _demo_plan, _in_gtk_context  # noqa: E402


def test_plan_overview_selects_by_title_and_retains_viewport_after_update() -> None:
    plan = _demo_plan()
    chosen = sorted(plan.nodes, key=lambda node: (node.key, node.id))[1]

    def check() -> None:
        page = PlanGraph(plan, lambda: None)
        network = page._network
        assert page._graph_stack.get_visible_child_name() == "map"
        assert page._graph_stack.get_child_by_name("dependencies") is not None
        assert network._choices.get_enable_search()
        model = network._choices.get_model()
        assert isinstance(model, Gtk.StringList)
        assert model.get_string(2) == f"{chosen.key} · {chosen.title}"
        network._choices.set_selected(2)
        assert page.selected_node == chosen.id

        before = dict(network._positions)
        width = network._area.get_content_width()
        toolbar = network.get_first_child()
        assert toolbar is not None
        choices = toolbar.get_first_child()
        assert choices is not None
        zoom_out = choices.get_next_sibling()
        assert zoom_out is not None
        zoom_in = zoom_out.get_next_sibling()
        assert zoom_in is not None
        _ = zoom_in.emit("clicked")  # pyright: ignore[reportAny]
        zoom_width = network._area.get_content_width()
        assert zoom_width > width

        states = tuple(
            replace(node, status=NodeStatus.VERIFYING) if node.id == chosen.id else node
            for node in reversed(plan.nodes)
        )
        assert page.update(replace(plan, nodes=states))
        assert page.selected_node == chosen.id
        assert network._choices.get_selected() == 2
        assert network._positions == before
        assert network._area.get_content_width() == zoom_width

    _in_gtk_context(check)
