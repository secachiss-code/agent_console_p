"""JSON Canvas 1.0 projection and durable inputs for the vault mirror."""

import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from aconsole.core.infrastructure.persistence.models import CanvasPosition, NodeAttempt, PlanNode
from aconsole.core.infrastructure.persistence.writer import Writer

_COLORS = {
    "pending": "6",
    "in_progress": "5",
    "verifying": "3",
    "done": "4",
    "blocked": "1",
    "failed": "1",
    "cancelled": "2",
}
_WIDTH = 320
_HEIGHT = 180
_MARGIN = 40
_COLUMN = 450
_ROW = 240


@dataclass(frozen=True, kw_only=True)
class CanvasNode:
    id: str
    key: str
    title: str
    status: str
    level: int = 1
    parent: str | None = None
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class AttemptLine:
    key: str
    attempt_no: int
    status: str
    verdict: str | None
    head_block: str | None

    def render(self) -> str:
        # Exactly one Markdown line, even for verdicts containing headings or newlines.
        verdict = " ".join((self.verdict or "").split())
        return f"- {self.attempt_no}/4 · {self.status} · {verdict} · {self.head_block or '-'}"


@dataclass(frozen=True, kw_only=True)
class CanvasHistory:
    """A read adapter. The same writer serializes snapshots with pipeline writes."""

    writer: Writer

    def positions(self, plan_id: str) -> dict[str, tuple[int, int]]:
        found: dict[str, tuple[int, int]] = {}

        def read(session: Session) -> None:
            for row in session.scalars(select(CanvasPosition).where(CanvasPosition.plan_id == plan_id)):
                found[row.node_id] = (row.x, row.y)

        _ = self.writer.call(read)
        return found

    def attempts(self, plan_id: str) -> tuple[AttemptLine, ...]:
        found: list[AttemptLine] = []

        def read(session: Session) -> None:
            rows = session.execute(
                select(PlanNode.node_key, NodeAttempt)
                .join(
                    NodeAttempt,
                    NodeAttempt.node_id == PlanNode.id,
                )
                .where(PlanNode.plan_id == plan_id)
                .order_by(PlanNode.node_key, NodeAttempt.attempt_no)
            )
            for key, attempt in rows:
                found.append(
                    AttemptLine(
                        key=key,
                        attempt_no=attempt.attempt_no,
                        status=attempt.status,
                        verdict=attempt.compact_verdict,
                        head_block=attempt.head_block,
                    )
                )

        _ = self.writer.call(read)
        return tuple(found)


def render_canvas(
    plan_id: str, nodes: tuple[CanvasNode, ...], positions: Mapping[str, tuple[int, int]] | None = None
) -> str:
    """Groups precede files in z order; only blocking dependencies become edges."""
    _safe_component(plan_id)
    by_key = {node.key: node for node in nodes}
    if len(by_key) != len(nodes) or len({node.id for node in nodes}) != len(nodes):
        message = "canvas nodes must have unique keys and ids"
        raise ValueError(message)
    directions = sorted((node for node in nodes if node.level == 0), key=lambda node: node.key)
    columns = {node.key: index for index, node in enumerate(directions)}
    for node in nodes:
        _safe_component(node.key)
    points = _layout_points(by_key, positions or {})
    rendered: list[dict[str, object]] = []
    for direction in directions:
        children = [points[node.id] for node in nodes if node.level != 0 and node.parent == direction.key]
        x = min((point[0] for point in children), default=columns[direction.key] * _COLUMN) - _MARGIN
        y = min((point[1] for point in children), default=0) - _MARGIN
        right = max((point[0] + _WIDTH for point in children), default=x + _WIDTH)
        bottom = max((point[1] + _HEIGHT for point in children), default=y + _HEIGHT)
        x, y = (positions or {}).get(direction.id, (x, y))
        rendered.append(
            {
                "id": direction.id,
                "type": "group",
                "label": direction.title,
                "x": x,
                "y": y,
                "width": max(_WIDTH, right - x + _MARGIN),
                "height": max(_HEIGHT, bottom - y + _MARGIN),
            }
        )
    for node in sorted(nodes, key=lambda node: node.key):
        if node.level == 0:
            continue
        x, y = points[node.id]
        rendered.append(
            {
                "id": node.id,
                "type": "file",
                "file": f"plans/{plan_id}/{node.key}.md",
                "x": x,
                "y": y,
                "width": _WIDTH,
                "height": _HEIGHT,
                "color": _COLORS.get(node.status, "6"),
            }
        )
    edges = _blocking_edges(nodes, by_key)
    return json.dumps({"nodes": rendered, "edges": edges}, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _layout_points(
    by_key: Mapping[str, CanvasNode],
    saved: Mapping[str, tuple[int, int]],
) -> dict[str, tuple[int, int]]:
    modules = {key: node for key, node in by_key.items() if node.level == 1}
    blockers = {key: set(node.depends_on) & modules.keys() for key, node in modules.items()}
    layers = _kahn_layers(blockers)
    grouped: dict[int, list[str]] = defaultdict(list)
    for key in sorted(modules):
        grouped[layers[key]].append(key)
    ranks: dict[str, int] = {}
    points: dict[str, tuple[int, int]] = {}
    for layer in sorted(grouped):
        scored: list[tuple[float, str]] = []
        for fallback, key in enumerate(grouped[layer]):
            previous = [ranks[parent] for parent in sorted(blockers[key]) if layers[parent] < layer and parent in ranks]
            score = sum(previous) / len(previous) if previous else float(fallback)
            scored.append((score, key))
        for row, (_score, key) in enumerate(sorted(scored)):
            node = modules[key]
            ranks[key] = row
            parent = by_key.get(node.parent) if node.parent is not None else None
            origin = saved.get(parent.id) if parent is not None and parent.level == 0 else None
            x, y = layer * _COLUMN, row * _ROW
            if origin is not None:
                x, y = x + origin[0] + _MARGIN, y + origin[1] + _MARGIN
            points[node.id] = saved.get(node.id, (x, y))
    return points


def _kahn_layers(blockers: Mapping[str, set[str]]) -> dict[str, int]:
    successors: dict[str, set[str]] = defaultdict(set)
    for key, dependencies in blockers.items():
        for dependency in dependencies:
            successors[dependency].add(key)
    indegrees = {key: len(dependencies) for key, dependencies in blockers.items()}
    frontier = sorted(key for key, count in indegrees.items() if count == 0)
    layers = dict.fromkeys(blockers, 0)
    visited: set[str] = set()
    while frontier:
        next_frontier: list[str] = []
        for key in frontier:
            visited.add(key)
            for successor in sorted(successors[key]):
                layers[successor] = max(layers[successor], layers[key] + 1)
                indegrees[successor] -= 1
                if indegrees[successor] == 0:
                    next_frontier.append(successor)
        frontier = sorted(next_frontier)
    # Corrupt cycles and their blocked descendants remain visible in layer 0.
    for key in blockers.keys() - visited:
        layers[key] = 0
    return layers


def _blocking_edges(nodes: tuple[CanvasNode, ...], by_key: Mapping[str, CanvasNode]) -> list[dict[str, object]]:
    edges: list[dict[str, object]] = []
    # L0 depends_on its L1 for closure; these are not module execution edges.
    for node in sorted(nodes, key=lambda node: node.key):
        if node.level == 0:
            continue
        for key in sorted(set(node.depends_on)):
            blocker = by_key.get(key)
            if blocker is not None and blocker.level == 1:
                edges.append(
                    {
                        "id": f"blocks:{blocker.id}:{node.id}",
                        "fromNode": blocker.id,
                        "toNode": node.id,
                        "fromSide": "right",
                        "toSide": "left",
                        "toEnd": "arrow",
                        "label": "blocks",
                    }
                )
    return edges


def _safe_component(value: str) -> None:
    path = PurePosixPath(value)
    invalid = not value or value in (".", "..") or len(path.parts) != 1 or path.is_absolute()
    if invalid or "\\" in value or "\x00" in value:
        message = "invalid vault path component"
        raise ValueError(message)
