"""Incremental graph snapshots through Writer, sharing module identity with facts."""

from datetime import UTC, datetime
from typing import Protocol, final, runtime_checkable

from sqlalchemy import Integer, String, delete, func, literal, select, tuple_, type_coerce
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aconsole.core.application.ports.plan_store import StoreWriteError
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph
from aconsole.core.infrastructure.persistence.models import KbEdge, KbEdgeKind, KbNode, KbType, Project
from aconsole.core.infrastructure.persistence.repositories.knowledge import code_ref_id
from aconsole.core.infrastructure.persistence.writer import Writer

_KINDS = frozenset({KbEdgeKind.IMPORTS, KbEdgeKind.USES_TYPE, KbEdgeKind.INHERITS})
_ORIGIN = "codegraph"
_MAX_HOPS = 3


@runtime_checkable
class _IndexWithStore(Protocol):
    @property
    def store(self) -> object: ...

    def graph(self, project_id: str) -> ExtractorGraph: ...

    def resolve(self, project_id: str, target: str) -> str | None: ...


@final
class CodeGraphDatabase:
    def __init__(self, writer: Writer) -> None:
        self._writer = writer

    def sync(self, project_id: str, graph: ExtractorGraph) -> None:
        try:
            _ = self._writer.call(lambda session: sync_graph(session, project_id, graph))
        except (SQLAlchemyError, ValueError, TimeoutError) as exc:
            message = f"code graph for {project_id} was not saved: {exc}"
            raise StoreWriteError(message) from exc

    def impact(
        self,
        project_id: str,
        target_id: str,
        hops: int,
        limit: int,
        kinds: frozenset[str] | None = None,
    ) -> tuple[list[tuple[str, int]], bool]:
        found: list[tuple[list[tuple[str, int]], bool]] = []
        _ = self._writer.call(
            lambda session: found.append(impact_rows(session, project_id, target_id, hops, limit, kinds))
        )
        return found[0]


def persistent_impact(  # noqa: PLR0913, PLR0917 -- existing impact request plus index dependency
    graph: object,
    project_id: str,
    start: str,
    hops: int,
    limit: int,
    kinds: frozenset[str] | None,
) -> tuple[list[list[str]], bool] | None:
    """Bridge the existing index/store assembly; None preserves memory-only indexes."""
    if not isinstance(graph, _IndexWithStore):
        return None
    store = graph.store
    if not isinstance(store, CodeGraphDatabase):
        return None
    try:
        resolved = graph.resolve(project_id, start)
        snapshot = graph.graph(project_id)  # Builds and commits before the CTE read.
    except DomainError:
        return None
    ids = {node.id: code_ref_id(project_id, node.path) for node in snapshot.nodes}
    if resolved is None or resolved not in ids:
        return None
    names = {stored: name for name, stored in ids.items()}
    rows, truncated = store.impact(project_id, ids[resolved], hops, limit, kinds)
    layers: list[list[str]] = []
    for depth in range(1, hops + 1):
        layer = sorted(names[identifier] for identifier, distance in rows if distance == depth and identifier in names)
        if layer:
            layers.append(layer)
    return layers, truncated


def impact_rows(  # noqa: PLR0913, PLR0917 -- existing impact request plus session dependency
    session: Session,
    project_id: str,
    target_id: str,
    hops: int,
    limit: int,
    kinds: frozenset[str] | None = None,
) -> tuple[list[tuple[str, int]], bool]:
    """Reverse import arrows, deduplicate diamonds/cycles and keep shortest distance.

    SQL UNION deduplicates (id, depth); the bounded depth terminates cycles and MIN
    assigns each dependent to its first layer. Fetch one extra row for truncation.
    """
    if not 0 <= hops <= _MAX_HOPS or limit < 0:
        message = "impact requires 0..3 hops and a nonnegative limit"
        raise ValueError(message)
    if not hops:
        return [], False
    allowed = _KINDS if kinds is None else frozenset(kind for kind in _KINDS if kind.value in kinds)
    if not allowed:
        return [], False
    # Correlated point lookups keep dst_id as the traversal index. Materializing
    # project-wide IN lists can make SQLite multiply every frontier by all modules.
    source_in_project = select(KbNode.id).where(KbNode.id == KbEdge.src_id, KbNode.project_id == project_id).exists()
    target_in_project = select(KbNode.id).where(KbNode.id == KbEdge.dst_id, KbNode.project_id == project_id).exists()
    valid = (KbEdge.kind.in_(allowed), source_in_project, target_in_project)
    reached = (
        select(KbEdge.src_id.label("id"), literal(1).label("depth"))
        .where(
            KbEdge.dst_id == target_id,
            *valid,
        )
        .cte("impact", recursive=True)
    )
    reached = reached.union(
        select(KbEdge.src_id, (reached.c.depth + 1).label("depth"))
        .join(
            reached,
            KbEdge.dst_id == reached.c.id,
        )
        .where(reached.c.depth < hops, *valid)
    )
    identifier = type_coerce(reached.c.id, String)
    depth = func.min(type_coerce(reached.c.depth, Integer))
    statement = (
        select(identifier, depth)
        .where(reached.c.id != target_id)
        .group_by(reached.c.id)
        .order_by(
            depth,
            reached.c.id,
        )
        .limit(limit + 1)
    )
    rows = [(row[0], row[1]) for row in session.execute(statement)]
    return rows[:limit], len(rows) > limit


def sync_graph(session: Session, project_id: str, graph: ExtractorGraph) -> None:
    """Remove only vanished codegraph edges; module nodes may still be referenced by facts."""
    if session.get(Project, project_id) is None:
        message = f"unknown project {project_id}"
        raise ValueError(message)
    identifiers = {node.id: code_ref_id(project_id, node.path) for node in graph.nodes}
    wanted: set[tuple[str, str, KbEdgeKind]] = set()
    for edge in graph.edges:
        if edge.kind not in _KINDS:
            continue
        if edge.source not in identifiers or edge.target not in identifiers:
            message = "code graph edge refers to an unknown node"
            raise ValueError(message)
        wanted.add((identifiers[edge.source], identifiers[edge.target], KbEdgeKind(edge.kind)))
    held_nodes = {node.id for node in session.scalars(select(KbNode).where(KbNode.project_id == project_id))}
    now = datetime.now(UTC)
    for node in graph.nodes:
        node_id = identifiers[node.id]
        if node_id not in held_nodes:
            session.add(
                KbNode(
                    id=node_id,
                    project_id=project_id,
                    node_type=KbType.MODULE,
                    title=node.path[:300],
                    source=node.path,
                    is_stale=False,
                    updated_at=now,
                )
            )
            held_nodes.add(node_id)
    session.flush()
    project_nodes = select(KbNode.id).where(KbNode.project_id == project_id)
    existing = session.scalars(select(KbEdge).where(KbEdge.src_id.in_(project_nodes))).all()
    held = {(edge.src_id, edge.dst_id, edge.kind) for edge in existing}
    removed = [
        (edge.src_id, edge.dst_id, edge.kind)
        for edge in existing
        if edge.origin == _ORIGIN and edge.kind in _KINDS and (edge.src_id, edge.dst_id, edge.kind) not in wanted
    ]
    if removed:
        _ = session.execute(
            delete(KbEdge)
            .where(
                KbEdge.origin == _ORIGIN,
                tuple_(KbEdge.src_id, KbEdge.dst_id, KbEdge.kind).in_(removed),
            )
            .returning(KbEdge.src_id, KbEdge.dst_id, KbEdge.kind)
        ).all()
    for source, target, kind in sorted(wanted - held):
        session.add(KbEdge(src_id=source, dst_id=target, kind=kind, origin=_ORIGIN))
