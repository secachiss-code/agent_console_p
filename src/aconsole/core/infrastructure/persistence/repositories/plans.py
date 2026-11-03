"""Plans through the single writer. A save replaces the plan's edges, lease, and artifact rows."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import final

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from aconsole.core.application.ports.plan_store import StoredArtifact, StoredNode, StoredPlan, StoreWriteError
from aconsole.core.domain.agent import AgentKind
from aconsole.core.domain.plan import NodeStatus
from aconsole.core.infrastructure.persistence.models import (
    ArtifactKind,
    EdgeKind,
    Lease,
    NodeArtifact,
    Plan,
    PlanEdge,
    PlanNode,
    PlanStatus,
    Project,
    Tab,
)
from aconsole.core.infrastructure.persistence.writer import Writer

_LOG = logging.getLogger(__name__)


@final
class PlanDatabase:
    """`PlanStore` on the `plans` tables."""

    def __init__(self, writer: Writer) -> None:
        self._writer: Writer = writer

    def load(self) -> tuple[StoredPlan, ...]:
        found: list[StoredPlan] = []

        def collect(session: Session) -> None:
            found.extend(load_plans(session))

        _ = self._writer.call(collect)
        return tuple(found)

    def save(self, plan: StoredPlan) -> None:
        try:
            _ = self._writer.call(lambda session: save_plan(session, plan))
        except (SQLAlchemyError, TimeoutError) as exc:
            message = f"plan {plan.id} was not saved: {exc}"
            raise StoreWriteError(message) from exc


def save_plan(session: Session, plan: StoredPlan) -> None:
    """Upsert one plan. A plan outside any added project has nowhere to live and stays in memory."""
    if session.get(Project, plan.project_id) is None:
        _LOG.warning("plan %s has no project row; it is not saved", plan.id)
        return
    row = session.get(Plan, plan.id)
    if row is None:
        row = Plan(id=plan.id, project_id=plan.project_id, created_at=plan.created_at)
        session.add(row)
    row.title = plan.title
    row.goal = plan.goal
    row.status = PlanStatus(plan.status)
    row.updated_at = plan.updated_at
    for node in plan.nodes:
        _save_node(session, plan, node)
    session.flush()
    ids = {node.key: node.id for node in plan.nodes}
    for node in plan.nodes:
        _ = session.execute(delete(PlanEdge).where(PlanEdge.dst_node_id == node.id))
        _ = session.execute(delete(Lease).where(Lease.node_id == node.id))
        _ = session.execute(delete(NodeArtifact).where(NodeArtifact.node_id == node.id))
    session.flush()
    for node in plan.nodes:
        _save_links(session, node, ids)


def load_plans(session: Session) -> tuple[StoredPlan, ...]:
    plans: list[StoredPlan] = []
    for row in session.scalars(select(Plan).order_by(Plan.created_at, Plan.id)).all():
        rows = session.scalars(select(PlanNode).where(PlanNode.plan_id == row.id).order_by(PlanNode.created_at)).all()
        keys = {node.id: node.node_key for node in rows}
        nodes = tuple(_stored_node(session, node, keys) for node in sorted(rows, key=_insertion))
        plans.append(
            StoredPlan(
                id=row.id,
                project_id=row.project_id,
                title=row.title,
                goal=row.goal,
                status=row.status.value,
                created_at=_aware(row.created_at),
                updated_at=_aware(row.updated_at),
                nodes=nodes,
            )
        )
    return tuple(plans)


def _insertion(row: PlanNode) -> tuple[datetime, int, str]:
    """`node-12` sorts after `node-2`: node ids count up, and many share one timestamp."""
    digits = row.id.rpartition("-")[2]
    return row.created_at, int(digits) if digits.isdigit() else 0, row.id


def _save_node(session: Session, plan: StoredPlan, node: StoredNode) -> None:
    row = session.get(PlanNode, node.id)
    if row is None:
        row = PlanNode(id=node.id, plan_id=plan.id, node_key=node.key, created_at=node.created_at)
        session.add(row)
    row.title = node.title
    row.objective = node.objective
    row.status = NodeStatus(node.status)
    row.owner_agent = None if node.owner is None else AgentKind(node.owner)
    row.paths = list(node.paths)
    row.acceptance = list(node.acceptance)
    row.review_required = node.review_required
    row.evidence = node.evidence
    row.boundaries = node.boundaries
    row.output_format = node.output_format
    row.agent_note = node.note
    row.layer = node.layer
    row.priority = node.priority
    row.unmet_blockers = node.unmet_blockers
    row.fence_seq = node.fence
    row.updated_at = plan.updated_at


def _save_links(session: Session, node: StoredNode, ids: dict[str, str]) -> None:
    for blocker in node.depends_on:
        session.add(PlanEdge(src_node_id=ids[blocker], dst_node_id=node.id, kind=EdgeKind.BLOCKS))
    if node.parent is not None and node.parent in ids:
        session.add(PlanEdge(src_node_id=ids[node.parent], dst_node_id=node.id, kind=EdgeKind.PARENT))
    for other in node.related:
        if other in ids:
            session.add(PlanEdge(src_node_id=ids[other], dst_node_id=node.id, kind=EdgeKind.RELATED))
    for artifact in node.artifacts:
        session.add(
            NodeArtifact(
                id=artifact.id,
                node_id=node.id,
                kind=ArtifactKind(artifact.kind),
                ref=artifact.ref,
                title=None,
                created_at=node.created_at,
            )
        )
    held = node.lease_id is not None and node.lease_expires_at is not None and node.tab_id is not None
    if held and session.get(Tab, node.tab_id) is not None:
        session.add(
            Lease(
                node_id=node.id,
                lease_id=node.lease_id,
                tab_id=node.tab_id,
                fence=node.fence,
                acquired_at=node.created_at,
                renewed_at=node.lease_expires_at,
                expires_at=node.lease_expires_at,
            )
        )


def _stored_node(session: Session, row: PlanNode, keys: dict[str, str]) -> StoredNode:
    edges = session.scalars(select(PlanEdge).where(PlanEdge.dst_node_id == row.id)).all()
    lease = session.get(Lease, row.id)
    artifacts = session.scalars(select(NodeArtifact).where(NodeArtifact.node_id == row.id)).all()
    parent = next((keys[edge.src_node_id] for edge in edges if edge.kind is EdgeKind.PARENT), None)
    return StoredNode(
        id=row.id,
        key=row.node_key,
        title=row.title,
        objective=row.objective,
        status=row.status.value,
        owner=None if row.owner_agent is None else row.owner_agent.value,
        paths=tuple(row.paths),
        acceptance=tuple(row.acceptance),
        review_required=row.review_required,
        boundaries=row.boundaries,
        output_format=row.output_format,
        evidence=row.evidence,
        note=row.agent_note,
        fence=row.fence_seq,
        tab_id=None if lease is None else lease.tab_id,
        lease_id=None if lease is None else lease.lease_id,
        lease_expires_at=None if lease is None else _aware(lease.expires_at),
        layer=row.layer,
        priority=row.priority,
        unmet_blockers=row.unmet_blockers,
        created_at=_aware(row.created_at),
        depends_on=tuple(keys[edge.src_node_id] for edge in edges if edge.kind is EdgeKind.BLOCKS),
        parent=parent,
        related=tuple(keys[edge.src_node_id] for edge in edges if edge.kind is EdgeKind.RELATED),
        artifacts=tuple(StoredArtifact(id=item.id, kind=item.kind.value, ref=item.ref) for item in artifacts),
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
