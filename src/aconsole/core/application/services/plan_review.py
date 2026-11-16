"""Human decision policy for the plan inbox; graph changes use the lifecycle service."""

from dataclasses import dataclass

from aconsole.core.application.ports.plan_store import StoredPlan, StoredPlanChange
from aconsole.core.domain.errors import DomainError


@dataclass(frozen=True, kw_only=True)
class ReviewMove:
    node_id: str | None
    target: str | None
    note: str
    replay: bool = False


def review_move(
    plan: StoredPlan,
    request: StoredPlanChange | None,
    *,
    action: str,
    note: str | None,
    decided_by: str,
) -> ReviewMove:
    request = _request(plan, request, decided_by)
    reason = (note or "").strip()
    if request.status != "open":
        if (
            request.decided_by == "human"
            and request.payload.get("decision") == action
            and request.payload.get("decision_note") == reason
        ):
            return ReviewMove(node_id=request.node_id, target=None, note=reason, replay=True)
        raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
    if action == "reject":
        return ReviewMove(node_id=request.node_id, target=None, note=reason)
    node = next((node for node in plan.nodes if node.id == request.node_id), None)
    if node is None:
        raise DomainError(code=4002, name="NODE_NOT_FOUND", retryable=False)
    if action == "unblock":
        if not reason:
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
        if node.status not in {"blocked", "failed"}:
            raise DomainError(code=4004, name="NODE_TRANSITION_INVALID", retryable=False)
        return ReviewMove(node_id=node.id, target="pending", note=reason)
    if action == "cancel":
        return ReviewMove(node_id=node.id, target="cancelled", note=reason)
    raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)


def _request(plan: StoredPlan, request: StoredPlanChange | None, decided_by: str) -> StoredPlanChange:
    if decided_by != "human":
        raise DomainError(code=4012, name="PLAN_ROLE", retryable=False)
    if request is None or request.plan_id != plan.id:
        raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
    return request
