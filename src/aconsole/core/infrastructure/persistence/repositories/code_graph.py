"""Incremental graph snapshots through Writer, sharing module identity with facts."""

from collections import deque
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, final, runtime_checkable

from sqlalchemy import Integer, String, delete, func, literal, select, tuple_, type_coerce
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aconsole.core.application.ports.plan_store import StoreWriteError
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.codegraph.index import CodeGraphIndex, impact_layers
from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph, GraphNode
from aconsole.core.infrastructure.codegraph.skeleton import FileHashes, file_hashes
from aconsole.core.infrastructure.persistence.models import KbEdge, KbEdgeKind, KbNode, KbType, Project
from aconsole.core.infrastructure.persistence.repositories.knowledge import code_ref_id
from aconsole.core.infrastructure.persistence.writer import Writer

_KINDS = frozenset(
    {KbEdgeKind.IMPORTS, KbEdgeKind.USES_TYPE, KbEdgeKind.INHERITS, KbEdgeKind.IMPLEMENTS, KbEdgeKind.DEPENDS_ON}
)
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

    def dependencies(self, project_id: str, targets: tuple[str, ...]) -> list[tuple[str, int]]:
        found: list[list[tuple[str, int]]] = []
        _ = self._writer.call(lambda session: found.append(dependency_rows(session, project_id, targets)))
        return found[0]


def dependency_rows(session: Session, project_id: str, targets: tuple[str, ...]) -> list[tuple[str, int]]:
    """Forward distance from all scoped files; bounded UNION terminates cycles."""
    if not targets:
        return []
    source_in_project = select(KbNode.id).where(KbNode.id == KbEdge.src_id, KbNode.project_id == project_id).exists()
    target_in_project = select(KbNode.id).where(KbNode.id == KbEdge.dst_id, KbNode.project_id == project_id).exists()
    allowed = _KINDS - {KbEdgeKind.USES_TYPE}
    valid = (KbEdge.kind.in_(allowed), source_in_project, target_in_project)
    reached = (
        select(KbEdge.dst_id.label("id"), literal(1).label("depth"))
        .where(KbEdge.src_id.in_(targets), *valid)
        .cte("dependencies", recursive=True)
    )
    reached = reached.union(
        select(KbEdge.dst_id, (reached.c.depth + 1).label("depth"))
        .join(reached, KbEdge.src_id == reached.c.id)
        .where(reached.c.depth < _MAX_HOPS, *valid)
    )
    depth = func.min(type_coerce(reached.c.depth, Integer))
    statement = (
        select(type_coerce(reached.c.id, String), depth)
        .join(KbNode, KbNode.id == reached.c.id)
        .where(reached.c.id.not_in(targets))
        .group_by(reached.c.id)
        .order_by(depth, KbNode.source, reached.c.id)
        .limit(40)
    )
    return [(row[0], row[1]) for row in session.execute(statement)]


def forward_dependencies(
    index: CodeGraphIndex,
    project_id: str,
    paths: tuple[str, ...],
    *,
    worktree: Path | None = None,
    snapshot: ExtractorGraph | None = None,
) -> tuple[tuple[GraphNode, int], ...]:
    """Use durable file identities when available, and the same BFS for transient indexes."""
    graph = index.graph(project_id, worktree=worktree) if snapshot is None else snapshot
    targets = {node.id for node in graph.nodes if node.path in paths}
    if (
        snapshot is None
        and isinstance(index.store, CodeGraphDatabase)
        and (worktree is None or index.is_project_root(project_id, worktree))
    ):
        rows = index.store.dependencies(project_id, tuple(sorted({code_ref_id(project_id, path) for path in paths})))
        by_file = dict(rows)
        distances = {
            node.id: by_file[code_ref_id(project_id, node.path)]
            for node in graph.nodes
            if code_ref_id(project_id, node.path) in by_file
        }
    else:
        distances = _forward_distances(graph, targets)
    # A file may carry several symbols; keep them together and count files, not symbols.
    files: dict[str, tuple[GraphNode, int]] = {}
    for node in sorted(graph.nodes, key=lambda item: (item.path, item.id)):
        if node.id not in distances or node.path in paths:
            continue
        depth = distances[node.id]
        merged = node
        previous = files.get(node.path)
        if previous is not None:
            held, old_depth = previous
            merged = GraphNode(
                id=held.id,
                path=held.path,
                name=held.name,
                kind=held.kind,
                symbols=tuple(sorted(set(held.symbols + node.symbols))),
            )
            depth = min(depth, old_depth)
        files[node.path] = (merged, depth)
    return tuple(sorted(files.values(), key=lambda item: (item[1], item[0].path))[:40])


def _forward_distances(snapshot: ExtractorGraph, targets: set[str]) -> dict[str, int]:
    distances: dict[str, int] = {}
    adjacency: dict[str, set[str]] = {}
    for edge in snapshot.edges:
        if edge.kind in _KINDS and edge.kind != KbEdgeKind.USES_TYPE:
            adjacency.setdefault(edge.source, set()).add(edge.target)
    queue = deque((identifier, 0) for identifier in sorted(targets))
    seen = set(targets)
    while queue:
        identifier, depth = queue.popleft()
        if depth >= _MAX_HOPS:
            continue
        for neighbor in sorted(adjacency.get(identifier, ())):
            if neighbor not in seen:
                seen.add(neighbor)
                distances[neighbor] = depth + 1
                queue.append((neighbor, depth + 1))
    return distances


def persistent_impact(  # noqa: PLR0913, PLR0917 -- existing impact request plus index dependency
    graph: object,
    project_id: str,
    start: str,
    hops: int,
    limit: int,
    kinds: frozenset[str] | None,
    *,
    worktree: Path | None = None,
) -> tuple[list[list[str]], bool] | None:
    """Use SQLite for the registered root and the isolated snapshot for a worker."""
    try:
        if (
            worktree is not None
            and isinstance(graph, CodeGraphIndex)
            and not graph.is_project_root(project_id, worktree)
        ):
            resolved = graph.resolve(project_id, start, worktree=worktree)
            if resolved is None:
                return None
            allowed = frozenset(kind.value for kind in _KINDS if kinds is None or kind.value in kinds)
            return impact_layers(graph.dependents(project_id, allowed, worktree=worktree), resolved, hops, limit)
        if not isinstance(graph, _IndexWithStore) or not isinstance(store := graph.store, CodeGraphDatabase):
            return None
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
    """Update module hashes and persist only the edge differences."""
    project = session.get(Project, project_id)
    if project is None:
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
    held_nodes = {
        node.id: node
        for node in session.scalars(
            select(KbNode).where(KbNode.project_id == project_id, KbNode.node_type == KbType.MODULE)
        )
    }
    invalid = _sync_nodes(session, project, graph, identifiers, held_nodes)
    session.flush()
    project_nodes = select(KbNode.id).where(KbNode.project_id == project_id)
    existing = session.scalars(
        select(KbEdge).where(KbEdge.src_id.in_(project_nodes) | KbEdge.dst_id.in_(project_nodes))
    ).all()
    held = {(edge.src_id, edge.dst_id, edge.kind) for edge in existing}
    present = set(identifiers.values())
    wanted = {edge for edge in wanted if edge[0] not in invalid}
    removed = [
        (edge.src_id, edge.dst_id, edge.kind)
        for edge in existing
        if edge.origin == _ORIGIN
        and edge.kind in _KINDS
        and edge.src_id in held_nodes
        and (edge.src_id, edge.dst_id, edge.kind) not in wanted
    ]
    if removed:
        _ = session.execute(
            delete(KbEdge).where(
                KbEdge.origin == _ORIGIN,
                tuple_(KbEdge.src_id, KbEdge.dst_id, KbEdge.kind).in_(removed),
            )
        )
    for source, target, kind in sorted(wanted - held):
        session.add(KbEdge(src_id=source, dst_id=target, kind=kind, origin=_ORIGIN))
    _remove_vanished(session, held_nodes, present, existing)


def _language(node: GraphNode) -> str | None:
    for prefix, language in (("ts:", "typescript"), ("rs:", "rust"), ("rs-crate:", "rust"), ("cs:", "csharp")):
        if node.id.startswith(prefix):
            return language
    return {
        ".py": "python",
        ".pyi": "python",
        ".rs": "rust",
        ".cs": "csharp",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "typescript",
        ".jsx": "typescript",
        ".mjs": "typescript",
        ".cjs": "typescript",
    }.get(Path(node.path).suffix)


def _sync_nodes(
    session: Session,
    project: Project,
    graph: ExtractorGraph,
    identifiers: dict[str, str],
    held: dict[str, KbNode],
) -> set[str]:
    invalid: set[str] = set()
    now = datetime.now(UTC)
    files: dict[str, list[GraphNode]] = {}
    for node in graph.nodes:
        files.setdefault(identifiers[node.id], []).append(node)
    for node_id, declarations in files.items():
        node = declarations[0]
        language = _language(node)
        symbols = tuple(symbol for declaration in declarations for symbol in declaration.symbols)
        current = held.get(node_id)
        previous = None
        if current is not None and current.language == language:
            previous = FileHashes(current.body_hash, current.interface_hash)
        hashes = file_hashes(Path(project.root_path), node.path, language, symbols, previous=previous)
        if current is None:
            current = KbNode(
                id=node_id,
                project_id=project.id,
                node_type=KbType.MODULE,
                title=node.path[:300],
                source=node.path,
                is_stale=False,
                updated_at=now,
            )
            session.add(current)
            held[node_id] = current
        if current.node_type is not KbType.MODULE:
            continue
        if language == "python" and hashes.interface == "":
            invalid.add(node_id)
        if (current.language, current.body_hash, current.interface_hash) != (language, hashes.body, hashes.interface):
            current.language, current.body_hash, current.interface_hash = language, hashes.body, hashes.interface
            current.updated_at = now
    return invalid


def _remove_vanished(session: Session, held: dict[str, KbNode], present: set[str], edges: Sequence[KbEdge]) -> None:
    referenced: set[str] = set()
    for edge in edges:
        if edge.origin != _ORIGIN or edge.kind not in _KINDS or edge.src_id not in held:
            referenced.update((edge.src_id, edge.dst_id))
    for node_id, node in held.items():
        if node_id in present or node.node_type is not KbType.MODULE:
            continue
        if node_id in referenced:
            if any(value is not None for value in (node.language, node.body_hash, node.interface_hash)):
                node.language, node.body_hash, node.interface_hash = None, None, None
                node.updated_at = datetime.now(UTC)
        else:
            session.delete(node)
