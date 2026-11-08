"""Verify a frozen real plan without access to writable application state."""

from pathlib import Path

import msgspec
import pytest
from tests.gui.front.test_b4a_plan import _in_gtk_context

from aconsole.contracts.app import PlanView
from aconsole.ui.viewmodels.logic.plan_layout import layout_plan, shown_nodes
from aconsole.ui.views.sidebar.plan_graph import PlanGraph

_EXPECTED_NODES = 83


def test_every_real_plan_node_has_a_widget_and_dependency_endpoint() -> None:
    plan = msgspec.json.decode(Path(__file__).with_name("plan-view.json").read_bytes(), type=PlanView)

    def check() -> None:
        graph = PlanGraph(plan, lambda: None)
        identities = {node.id for node in plan.nodes}
        if len(identities) != _EXPECTED_NODES:
            pytest.fail("the frozen real plan should contain every node")
        if (
            set(graph.node_buttons) != identities
            or {item.node_id for item in layout_plan(shown_nodes(plan.nodes))} != identities
        ):
            pytest.fail("a real node is absent from the native page or its layout")
        if any(parent not in identities for node in plan.nodes for parent in node.depends_on):
            pytest.fail("a dependency endpoint is missing")
        for node in (plan.nodes[0], plan.nodes[-1]):
            graph.node_buttons[node.id].emit("clicked")
            if node.key not in graph.details_label.get_text():
                pytest.fail("activating the node did not show its details")

    _in_gtk_context(check)
