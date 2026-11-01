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
    assert by_id["source-b"].y == 76
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
