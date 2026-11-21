"""Project stored plan relationships into an overview or a local graph."""

from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

import msgspec
from pydantic import BaseModel

type PlanEdgeKind = Literal["decomposes", "depends_on", "related"]
type PlanSection = Literal["contract", "relations", "attempts", "reviews", "artifacts"]
_INLINE_BYTES = 4096


@dataclass(frozen=True, kw_only=True)
class PlanGraphQuery:
    plan_id: str
    mode: Literal["overview", "local"] = "overview"
    anchor: str | None = None
    depth: int = 1
    edge_kinds: tuple[PlanEdgeKind, ...] = ()
    limit: int = 12
    cursor: str | None = None


class PlanVertex(BaseModel, frozen=True):
    node_id: str
    key: str
    title: str
    status: str
    ready: bool
    level: int
    pipeline: bool = False
    parent: str | None = None
    children: int = 0


class PlanEdge(BaseModel, frozen=True):
    source: str
    target: str
    kind: PlanEdgeKind


class PlanGraphView(BaseModel, frozen=True):
    plan_id: str
    title: str
    phase: str
    revision: str
    nodes: tuple[PlanVertex, ...]
    edges: tuple[PlanEdge, ...]
    boundary: tuple[PlanVertex, ...] = ()
    cursor: str | None = None


class PlanNodeEntry(BaseModel, frozen=True):
    section: PlanSection
    field: str
    value: object
    json_part: int | None = None
    json_parts: int | None = None


class PlanNodeDetails(BaseModel, frozen=True):
    plan_id: str
    revision: str
    node: PlanVertex
    entries: tuple[PlanNodeEntry, ...] = ()
    cursor: str | None = None


def detail_entries(section: PlanSection, field: str, value: object) -> tuple[PlanNodeEntry, ...]:
    encoded = msgspec.json.encode(value)
    if len(encoded) <= _INLINE_BYTES:
        return (PlanNodeEntry(section=section, field=field, value=value),)
    # Chunk the JSON value, keeping escapes and multibyte characters intact on reassembly.
    text = encoded.decode()
    chunk_size = 512
    parts = (len(text) + chunk_size - 1) // chunk_size
    return tuple(
        PlanNodeEntry(section=section, field=field, value=text[start:start + chunk_size],
                      json_part=part, json_parts=parts)
        for part, start in enumerate(range(0, len(text), chunk_size), start=1)
    )


def overview(
    nodes: tuple[PlanVertex, ...], edges: Iterable[PlanEdge]
) -> tuple[tuple[PlanVertex, ...], tuple[PlanEdge, ...]]:
    """Show roots and roll up connections between their descendants."""
    children = Counter(node.parent for node in nodes)
    by_id = {node.node_id: node for node in nodes}
    roots = tuple(
        node.model_copy(update={"children": children[node.node_id]}) for node in nodes if node.parent is None
    )

    def root_id(node_id: str) -> str:
        parent = by_id[node_id].parent
        while parent is not None:
            node_id = parent
            parent = by_id[node_id].parent
        return node_id

    projected: dict[tuple[str, str, PlanEdgeKind], PlanEdge] = {}
    for edge in edges:
        source, target = root_id(edge.source), root_id(edge.target)
        if source == target:
            continue
        if edge.kind == "related" and source > target:
            source, target = target, source
        projected[source, target, edge.kind] = PlanEdge(source=source, target=target, kind=edge.kind)
    return roots, tuple(projected.values())


def neighborhood(
    nodes: tuple[PlanVertex, ...], edges: tuple[PlanEdge, ...], anchor: str, depth: int
) -> tuple[tuple[PlanVertex, ...], tuple[PlanEdge, ...]]:
    """Visit each reachable vertex once, in distance order, preserving edge direction."""
    by_id = {node.node_id: node for node in nodes}
    adjacent: dict[str, set[str]] = {node.node_id: set() for node in nodes}
    children = Counter(node.parent for node in nodes)
    for edge in edges:
        adjacent[edge.source].add(edge.target)
        adjacent[edge.target].add(edge.source)
    seen = {anchor}
    selected: list[PlanVertex] = []
    pending = deque([(anchor, 0)])
    while pending:
        node_id, distance = pending.popleft()
        node = by_id[node_id]
        selected.append(node)
        if distance == depth:
            continue
        for neighbor in sorted(adjacent[node_id], key=lambda identifier: by_id[identifier].key):
            if neighbor not in seen:
                seen.add(neighbor)
                pending.append((neighbor, distance + 1))
    visible_children = Counter(node.parent for node in selected)
    selected = [
        node.model_copy(update={"children": children[node.node_id] - visible_children[node.node_id]})
        for node in selected
    ]
    return tuple(selected), tuple(edge for edge in edges if edge.source in seen and edge.target in seen)
