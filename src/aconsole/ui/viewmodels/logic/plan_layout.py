"""Deterministic layer layout for project plan previews."""

from collections.abc import Sequence
from dataclasses import dataclass

from aconsole.contracts.app import PlanNodeView
from aconsole.contracts.common import NodeId


@dataclass(frozen=True, slots=True)
class PlanNodePosition:
    node_id: NodeId
    x: int
    y: int


def layout_plan(nodes: tuple[PlanNodeView, ...]) -> tuple[PlanNodePosition, ...]:
    if any(node.level == 0 or node.parent is not None for node in nodes):
        return _grouped(nodes)
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
            positions.append(PlanNodePosition(node.id, layer * 160 + 12, rank * 84 + 12))
    return tuple(positions)


def shown_nodes(nodes: Sequence[PlanNodeView]) -> tuple[PlanNodeView, ...]:
    """Directions are group headings; only modules become graph vertices."""
    return tuple(node for node in nodes if node.level == 1)


def direction_columns(nodes: tuple[PlanNodeView, ...]) -> tuple[tuple[NodeId | None, str], ...]:
    directions = sorted((node for node in nodes if node.level == 0), key=lambda node: node.key)
    columns: list[tuple[NodeId | None, str]] = [(node.id, f"{node.key} · {node.title}") for node in directions]
    known = {node.id for node in directions}
    if any(node.level == 1 and node.parent not in known for node in nodes):
        columns.append((None, ""))
    return tuple(columns)


def _grouped(nodes: tuple[PlanNodeView, ...]) -> tuple[PlanNodePosition, ...]:
    columns = direction_columns(nodes)
    known = {identifier for identifier, _title in columns}
    positions: list[PlanNodePosition] = []
    for column, (identifier, _title) in enumerate(columns):
        members = sorted(
            (node for node in shown_nodes(nodes) if (node.parent if node.parent in known else None) == identifier),
            key=lambda node: (node.layer, node.key),
        )
        positions.extend(
            PlanNodePosition(node.id, column * 196 + 12, rank * 112 + 44) for rank, node in enumerate(members)
        )
    return tuple(positions)
