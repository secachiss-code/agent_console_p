"""Deterministic layer layout for project plan previews."""

from collections.abc import Sequence
from dataclasses import dataclass

from aconsole.contracts.app import PlanNodeView
from aconsole.contracts.common import NodeId, NodeStatus


@dataclass(frozen=True, slots=True)
class PlanNodePosition:
    node_id: NodeId
    x: int
    y: int


def layout_plan(nodes: tuple[PlanNodeView, ...]) -> tuple[PlanNodePosition, ...]:
    layers = sorted({node.layer for node in nodes})
    ranks: dict[NodeId, int] = {}
    positions: list[PlanNodePosition] = []
    for layer in layers:
        group = sorted((node for node in nodes if node.layer == layer), key=lambda node: node.key)

        scored: list[tuple[float, str, PlanNodeView]] = []
        for fallback, node in enumerate(group):
            upstream = [ranks[parent] for parent in node.depends_on if parent in ranks]
            score = sum(upstream) / len(upstream) if upstream else float(fallback)
            scored.append((score, node.key, node))
        ordered = [item[2] for item in sorted(scored, key=lambda item: (item[0], item[1]))]
        for rank, node in enumerate(ordered):
            ranks[node.id] = rank
            positions.append(PlanNodePosition(node.id, layer * 160 + 12, rank * 64 + 12))
    return tuple(positions)


SHOWN_NODES = 30
_FIRST_SHOWN = (NodeStatus.VERIFYING, NodeStatus.IN_PROGRESS)


def shown_nodes(nodes: Sequence[PlanNodeView]) -> tuple[PlanNodeView, ...]:
    """The nodes the page has room for, in plan order. One waiting for a person is never left out."""
    wanted = [node for node in nodes if node.status in _FIRST_SHOWN][:SHOWN_NODES]
    chosen = {node.id for node in wanted}
    for node in nodes:
        if len(chosen) >= SHOWN_NODES:
            break
        chosen.add(node.id)
    return tuple(node for node in nodes if node.id in chosen)
