"""The ready list must respect the same path reservations as a claim."""
from datetime import UTC, datetime

import pytest

from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.plan import Move, NodeSpec, NodeStatus, Plan, Role

AT = datetime(2026, 11, 5, tzinfo=UTC)


@pytest.mark.parametrize("verifying", [False, True])
def test_plan_ready_paths_excludes_reserved_subtrees_until_released(*, verifying: bool) -> None:
    plan = Plan()
    plan.add(NodeSpec(key="owner", created_at=AT, paths=("src/core/**",)))
    plan.add(NodeSpec(key="waiting", created_at=AT, paths=("src/core/worker.py",)))
    plan.add(NodeSpec(key="independent", created_at=AT, paths=("src/ui",)))
    owner = plan.claim("owner", tab_id="first", at=AT)
    if verifying:
        owner = plan.transition(
            Move(key="owner", to=NodeStatus.VERIFYING, role=Role.AGENT,
                 at=AT, fence=owner.fence, evidence="tests passed")
        )
    assert not plan.ready("waiting")
    assert plan.ready("independent")
    with pytest.raises(DomainError, match="PATHS_OVERLAP"):
        plan.claim("waiting", tab_id="second", at=AT)
    plan.transition(
        Move(key="owner", to=NodeStatus.DONE, role=Role.HUMAN,
             at=AT, fence=owner.fence, evidence="accepted")
    )
    assert plan.ready("waiting")
    assert plan.claim("waiting", tab_id="second", at=AT).status is NodeStatus.IN_PROGRESS


def test_plan_ready_paths_empty_reservations_do_not_block() -> None:
    plan = Plan()
    plan.add(NodeSpec(key="owner", created_at=AT))
    plan.add(NodeSpec(key="waiting", created_at=AT, paths=("src/core/**",)))
    plan.claim("owner", tab_id="first", at=AT)
    assert plan.ready("waiting")
