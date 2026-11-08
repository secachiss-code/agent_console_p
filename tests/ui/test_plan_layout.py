"""Stable layered layout for the compact plan graph."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import msgspec
import pytest

if TYPE_CHECKING:
    from aconsole.contracts.app import PlanNodeView


def _node(
    node_id: str, key: str, layer: int, depends_on: tuple[str, ...] = ()
) -> PlanNodeView:
    from aconsole.contracts.common import NodeId
    from aconsole.core.infrastructure.fake.scenarios import demo_snapshot

    template = demo_snapshot().plans[0].nodes[0]
    return msgspec.structs.replace(
        template,
        id=NodeId(node_id),
        key=key,
        title=f"Title {key}",
        layer=layer,
        depends_on=tuple(NodeId(value) for value in depends_on),
    )


def test_b4a_plan_layout_empty_input_returns_empty_output() -> None:
    from aconsole.ui.viewmodels.logic.plan_layout import layout_plan

    assert layout_plan(()) == ()


def test_b4a_plan_layout_uses_layers_upstream_barycenters_and_key_ties() -> None:
    from aconsole.ui.viewmodels.logic.plan_layout import layout_plan

    nodes = (
        _node("source-b", "B", 0),
        _node("source-a", "A", 0),
        _node("right", "right", 1, ("source-b",)),
        _node("tie-b", "tie-b", 1, ("source-a",)),
        _node("tie-a", "tie-a", 1, ("source-a",)),
        _node("middle", "middle", 1, ("source-a", "source-b")),
    )

    positions = layout_plan(nodes)
    by_id = {str(position.node_id): position for position in positions}

    assert by_id["source-a"].x == 12
    assert by_id["source-b"].x == 12
    assert by_id["tie-a"].x == 172
    assert by_id["right"].x == 172
    assert by_id["source-a"].y == 12
    assert by_id["source-b"].y == 96
    assert by_id["tie-a"].y < by_id["tie-b"].y
    assert by_id["tie-b"].y < by_id["middle"].y
    assert by_id["middle"].y < by_id["right"].y


def test_b4a_plan_layout_ignores_missing_dependency_ids() -> None:
    from aconsole.ui.viewmodels.logic.plan_layout import layout_plan

    nodes = (
        _node("source", "source", 0),
        _node("child", "child", 1, ("missing", "source")),
        _node("orphan", "orphan", 1, ("not-present",)),
    )

    positions = layout_plan(nodes)

    assert len(positions) == len(nodes)
    assert {str(position.node_id) for position in positions} == {
        "source",
        "child",
        "orphan",
    }
    assert next(position for position in positions if position.node_id == "child").y == 12


def test_b4a_plan_layout_is_deterministic_and_does_not_change_input() -> None:
    from aconsole.ui.viewmodels.logic.plan_layout import PlanNodePosition, layout_plan

    nodes = (
        _node("source", "source", 0),
        _node("second", "second", 1, ("source",)),
        _node("first", "first", 1, ("source",)),
    )
    original = nodes

    positions = layout_plan(nodes)

    assert nodes is original
    assert nodes == original
    assert layout_plan(nodes) == positions
    assert all(isinstance(position, PlanNodePosition) for position in positions)
    with pytest.raises(FrozenInstanceError):
        setattr(positions[0], "x", 900)  # noqa: B010 -- intentionally verify the frozen value rejects mutation.


def test_shown_nodes_preserve_every_input_node_in_order() -> None:
    from aconsole.contracts.common import NodeStatus
    from aconsole.ui.viewmodels.logic.plan_layout import shown_nodes

    nodes = [
        msgspec.structs.replace(_node(f"id-{index}", f"N-{index}", 0), status=NodeStatus.PENDING)
        for index in range(40)
    ]
    nodes[-1] = msgspec.structs.replace(nodes[-1], status=NodeStatus.VERIFYING)
    shown = shown_nodes(nodes)
    assert len(shown) == len(nodes)
    assert shown == tuple(nodes)
    assert shown[-1] is nodes[-1]


def test_shown_nodes_keep_a_short_plan_whole_and_in_order() -> None:
    from aconsole.ui.viewmodels.logic.plan_layout import shown_nodes

    nodes = [_node(f"id-{index}", f"N-{index}", 0) for index in range(3)]
    assert shown_nodes(nodes) == tuple(nodes)


def test_large_plan_keeps_all_nodes_and_dependency_endpoints_visible() -> None:
    from aconsole.ui.viewmodels.logic.plan_layout import layout_plan, shown_nodes

    nodes = tuple(
        _node(
            f"id-{index}",
            f"N-{index:02}",
            0 if index == 0 else 1,
            ("id-0",) if index == 71 else (),
        )
        for index in range(72)
    )

    shown = shown_nodes(nodes)
    positions = layout_plan(shown)
    visible_ids = {position.node_id for position in positions}

    assert shown == nodes
    assert len(positions) == len(nodes)
    assert nodes[71].depends_on == (nodes[0].id,)
    assert nodes[0].id in visible_ids
    assert nodes[71].id in visible_ids
