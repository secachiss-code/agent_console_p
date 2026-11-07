"""The ready list must respect the same path reservations as a claim."""
from datetime import UTC, datetime

import pytest

from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.plan import Move, NodeSpec, NodeStatus, Plan, Role
from aconsole.core.infrastructure.mcp.tools import NodeIn, PlanBoard, TabContext

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


@pytest.mark.parametrize("verifying", [False, True])
def test_public_ready_filters_reservations_before_applying_limit(*, verifying: bool) -> None:
    board = PlanBoard(now=lambda: AT)
    owner = TabContext(tab_id="owner", project_id="project", worktree=None, agent="codex")
    waiting = TabContext(tab_id="waiting", project_id="project", worktree=None, agent="codex")
    created = board.create(owner, "Path reservations", "Keep ready nodes claimable", [
        NodeIn(key="OWN-1", title="Owner", objective="Work", paths=["src/core/**"]),
        NodeIn(key="A-1", title="Waiting", objective="Work", paths=["src/core/worker.py"]),
        NodeIn(key="Z-1", title="Independent", objective="Work", paths=["src/ui/**"]),
    ])
    nodes = {node.key: node.node_id for node in created.nodes}
    claimed = board.claim(owner, nodes["OWN-1"])
    if verifying:
        board.update(owner, nodes["OWN-1"], claimed.fence, "verifying", None, "checks passed")
    assert [node.key for node in board.ready(waiting, created.plan_id, 1).nodes] == ["Z-1"]
    board.update(owner, nodes["OWN-1"], claimed.fence, "failed", "Release after failed work", None)
    assert [node.key for node in board.ready(waiting, created.plan_id, 1).nodes] == ["A-1"]
    assert board.claim(waiting, nodes["A-1"]).fence > 0
