"""Incremental graph snapshots through Writer, sharing module identity with facts."""

from datetime import UTC, datetime
from typing import final

from sqlalchemy import delete, select, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aconsole.core.application.ports.plan_store import StoreWriteError
from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph
from aconsole.core.infrastructure.persistence.models import KbEdge, KbEdgeKind, KbNode, KbType, Project
from aconsole.core.infrastructure.persistence.repositories.knowledge import code_ref_id
from aconsole.core.infrastructure.persistence.writer import Writer

_KINDS = frozenset({KbEdgeKind.IMPORTS, KbEdgeKind.USES_TYPE, KbEdgeKind.INHERITS})
_ORIGIN = "codegraph"


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
            session.add(KbNode(id=node_id, project_id=project_id, node_type=KbType.MODULE,
                               title=node.path[:300], source=node.path, is_stale=False, updated_at=now))
            held_nodes.add(node_id)
    session.flush()
    project_nodes = select(KbNode.id).where(KbNode.project_id == project_id)
    existing = session.scalars(select(KbEdge).where(KbEdge.src_id.in_(project_nodes))).all()
    held = {(edge.src_id, edge.dst_id, edge.kind) for edge in existing}
    removed = [(edge.src_id, edge.dst_id, edge.kind) for edge in existing
               if edge.origin == _ORIGIN and edge.kind in _KINDS
               and (edge.src_id, edge.dst_id, edge.kind) not in wanted]
    if removed:
        _ = session.execute(delete(KbEdge).where(
            KbEdge.origin == _ORIGIN,
            tuple_(KbEdge.src_id, KbEdge.dst_id, KbEdge.kind).in_(removed),
        ).returning(KbEdge.src_id, KbEdge.dst_id, KbEdge.kind)).all()
    for source, target, kind in sorted(wanted - held):
        session.add(KbEdge(src_id=source, dst_id=target, kind=kind, origin=_ORIGIN))
