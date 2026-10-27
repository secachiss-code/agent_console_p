"""Every plan-status cell, plus leases, cycles, priority, and layout."""

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.plan import (
    Move,
    NodeSpec,
    NodeStatus,
    Plan,
    Role,
    globs_overlap,
)

AT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)

BOTH = "both"
HUMAN = "human"
EVIDENCE = "evidence"
NOTE = "note"
CLAIM = "claim"
DONE = "done"

SPEC: dict[NodeStatus, dict[NodeStatus, str]] = {
    NodeStatus.PENDING: {
        NodeStatus.IN_PROGRESS: CLAIM,
        NodeStatus.BLOCKED: BOTH,
        NodeStatus.CANCELLED: HUMAN,
    },
    NodeStatus.IN_PROGRESS: {
        NodeStatus.PENDING: BOTH,
        NodeStatus.VERIFYING: EVIDENCE,
        NodeStatus.BLOCKED: NOTE,
        NodeStatus.DONE: DONE,
        NodeStatus.FAILED: NOTE,
        NodeStatus.CANCELLED: HUMAN,
    },
    NodeStatus.VERIFYING: {
        NodeStatus.IN_PROGRESS: BOTH,
        NodeStatus.DONE: HUMAN,
        NodeStatus.FAILED: BOTH,
        NodeStatus.CANCELLED: HUMAN,
    },
    NodeStatus.BLOCKED: {
        NodeStatus.PENDING: BOTH,
        NodeStatus.CANCELLED: HUMAN,
    },
    NodeStatus.FAILED: {
        NodeStatus.PENDING: BOTH,
        NodeStatus.CANCELLED: HUMAN,
    },
    NodeStatus.DONE: {NodeStatus.PENDING: HUMAN},
    NodeStatus.CANCELLED: {NodeStatus.PENDING: HUMAN},
}


def _spec(
    key: str,
    depends_on: tuple[str, ...] = (),
    paths: tuple[str, ...] = (),
    *,
    review_required: bool = False,
) -> NodeSpec:
    return NodeSpec(
        key=key,
        created_at=AT,
        depends_on=depends_on,
        paths=paths,
        review_required=review_required,
    )


def _move(to: NodeStatus, role: Role, *, at: datetime, fence: int | None, kind: str) -> Move:
    return Move(
        key="N-1",
        to=to,
        role=role,
        at=at,
        tab_id="tab" if kind == CLAIM or to is NodeStatus.IN_PROGRESS else None,
        fence=fence,
        evidence="shipped" if kind in {EVIDENCE, DONE} else None,
        note="because" if kind == NOTE else None,
    )


def _arrive(status: NodeStatus) -> tuple[Plan, int | None]:
    plan = Plan()
    plan.add(_spec("N-1"))
    if status is NodeStatus.PENDING:
        return plan, None
    if status is NodeStatus.BLOCKED:
        plan.transition(Move(key="N-1", to=NodeStatus.BLOCKED, role=Role.AGENT, at=AT))
        return plan, None
    if status is NodeStatus.CANCELLED:
        plan.transition(Move(key="N-1", to=NodeStatus.CANCELLED, role=Role.HUMAN, at=AT))
        return plan, None
    fence = plan.claim("N-1", tab_id="tab", at=AT).fence
    later = AT + timedelta(seconds=1)
    follow = {
        NodeStatus.VERIFYING: Move(
            key="N-1", to=NodeStatus.VERIFYING, role=Role.AGENT, at=later, fence=fence, evidence="shipped"
        ),
        NodeStatus.FAILED: Move(
            key="N-1", to=NodeStatus.FAILED, role=Role.AGENT, at=later, fence=fence, note="because"
        ),
        NodeStatus.DONE: Move(
            key="N-1", to=NodeStatus.DONE, role=Role.AGENT, at=later, fence=fence, evidence="shipped"
        ),
    }
    step = follow.get(status)
    if step is not None:
        plan.transition(step)
    kept = fence if status in {NodeStatus.IN_PROGRESS, NodeStatus.VERIFYING} else None
    return plan, kept


@pytest.mark.parametrize("source", list(NodeStatus))
@pytest.mark.parametrize("target", list(NodeStatus))
def test_transition_cell(source: NodeStatus, target: NodeStatus) -> None:
    plan, fence = _arrive(source)
    kind = SPEC.get(source, {}).get(target)
    at = AT + timedelta(seconds=2)
    if kind is None:
        with pytest.raises(DomainError, match="NODE_TRANSITION_INVALID"):
            plan.transition(_move(target, Role.AGENT, at=at, fence=fence, kind=BOTH))
        assert plan.get("N-1").status is source
        return
    if kind == HUMAN:
        with pytest.raises(DomainError, match="NODE_TRANSITION_INVALID"):
            plan.transition(_move(target, Role.AGENT, at=at, fence=fence, kind=kind))
        moved = plan.transition(_move(target, Role.HUMAN, at=at, fence=fence, kind=kind))
    else:
        moved = plan.transition(_move(target, Role.AGENT, at=at, fence=fence, kind=kind))
    assert moved.status is target


def test_claim_reports_unmet_blockers() -> None:
    plan = Plan()
    plan.add(_spec("N-1"))
    plan.add(_spec("N-2", depends_on=("N-1",)))
    with pytest.raises(DomainError, match="NODE_NOT_READY") as caught:
        plan.claim("N-2", tab_id="tab", at=AT)
    assert caught.value.extra == ("N-1",)
    assert plan.ready("N-2") is False
    claimed = plan.claim("N-1", tab_id="tab", at=AT)
    plan.transition(
        Move(
            key="N-1",
            to=NodeStatus.DONE,
            role=Role.AGENT,
            at=AT + timedelta(seconds=2),
            fence=claimed.fence,
            evidence="shipped",
        )
    )
    assert plan.ready("N-2") is True


def test_review_required_blocks_agent_done_until_a_human_verifies() -> None:
    plan = Plan()
    plan.add(_spec("N-1", review_required=True))
    claimed = plan.claim("N-1", tab_id="tab", at=AT)
    with pytest.raises(DomainError, match="NODE_TRANSITION_INVALID"):
        plan.transition(
            Move(
                key="N-1",
                to=NodeStatus.DONE,
                role=Role.HUMAN,
                at=AT + timedelta(seconds=1),
                fence=claimed.fence,
                evidence="shipped",
            )
        )
    plan.transition(
        Move(
            key="N-1",
            to=NodeStatus.VERIFYING,
            role=Role.AGENT,
            at=AT + timedelta(seconds=1),
            fence=claimed.fence,
            evidence="shipped",
        )
    )
    done = plan.transition(
        Move(key="N-1", to=NodeStatus.DONE, role=Role.HUMAN, at=AT + timedelta(seconds=2), fence=claimed.fence)
    )
    assert done.status is NodeStatus.DONE
    assert done.worktree_id == "tab"
    assert done.tab_id is None


def test_lease_fence_overlap_and_expiry() -> None:
    plan = Plan()
    plan.add(_spec("N-1", paths=("src/**",)))
    plan.add(_spec("N-2", paths=("src/app/**",)))
    first = plan.claim("N-1", tab_id="tab-a", at=AT)
    with pytest.raises(DomainError, match="LEASE_HELD"):
        plan.claim("N-1", tab_id="tab-b", at=AT + timedelta(seconds=1))
    with pytest.raises(DomainError, match="PATHS_OVERLAP"):
        plan.claim("N-2", tab_id="tab-b", at=AT + timedelta(seconds=1))
    with pytest.raises(DomainError, match="LEASE_STALE_FENCE") as stale:
        plan.heartbeat("N-1", fence=first.fence + 1, at=AT + timedelta(seconds=1))
    assert stale.value.extra == (str(first.fence),)
    plan.heartbeat("N-1", fence=first.fence, at=AT + timedelta(minutes=10))
    released = plan.expire_leases(AT + timedelta(minutes=30))
    assert released == ("N-1",)
    node = plan.get("N-1")
    assert node.status is NodeStatus.PENDING
    assert node.tab_id is None
    assert node.worktree_id == "tab-a"
    with pytest.raises(DomainError, match="LEASE_EXPIRED"):
        plan.heartbeat("N-1", fence=first.fence, at=AT + timedelta(minutes=30))


def test_parent_edge_does_not_block_ready() -> None:
    plan = Plan()
    plan.add(_spec("N-1"))
    plan.add(NodeSpec(key="N-2", created_at=AT, parent="N-1"))
    assert plan.ready("N-2") is True


def test_priority_is_longest_path_plus_fanout_plus_age() -> None:
    plan = Plan()
    plan.add(_spec("A"))
    plan.add(_spec("B", depends_on=("A",)))
    plan.add(_spec("C", depends_on=("B",)))
    later = AT + timedelta(days=2)
    assert plan.priority("C", later) == pytest.approx(0.02)
    assert plan.priority("B", later) == pytest.approx(1 + 0.1 + 0.02)
    assert plan.priority("A", later) == pytest.approx(2 + 0.1 + 0.02)


def test_kahn_orders_blockers_first_and_a_cycle_has_a_closed_path() -> None:
    plan = Plan()
    plan.add(_spec("A"))
    plan.add(_spec("B", depends_on=("A",)))
    plan.add(_spec("C", depends_on=("B",)))
    assert plan.kahn() == ("A", "B", "C")
    assert plan.cycle_path() is None
    with pytest.raises(DomainError, match="PLAN_CYCLE") as caught:
        plan.depend("A", "C")
    assert caught.value.extra[0] == caught.value.extra[-1]
    assert set(caught.value.extra) >= {"A", "C"}
    assert plan.cycle_path() is None


def test_layout_barycenter_and_pinned_card() -> None:
    plan = Plan()
    plan.add(_spec("A"))
    plan.add(_spec("B"))
    plan.add(_spec("D", depends_on=("A",)))
    plan.add(_spec("C", depends_on=("B",)))
    places = {key: (layer, index) for key, layer, index in plan.layout()}
    assert places["D"][0] == 1
    assert places["D"][1] < places["C"][1]
    pinned = Plan()
    pinned.add(_spec("A"))
    pinned.add(_spec("B"))
    pinned.add(NodeSpec(key="C", created_at=AT, depends_on=("B",), user_moved=True, pin=0))
    pinned.add(_spec("D", depends_on=("A",)))
    pinned_places = {key: index for key, layer, index in pinned.layout() if layer == 1}
    assert pinned_places["C"] == 0


@pytest.mark.parametrize(
    ("left", "right", "overlaps"),
    [
        ("src/**", "src/app/**", True),
        ("src/a.py", "src/b.py", False),
        ("src/**", "tests/**", False),
        ("*.py", "a.py", True),
    ],
)
def test_glob_overlap(left: str, right: str, *, overlaps: bool) -> None:
    assert globs_overlap(left, right) is overlaps
    assert globs_overlap(right, left) is overlaps


@given(st.integers(min_value=1, max_value=6))
@settings(max_examples=40)
def test_forward_edges_are_acyclic(count: int) -> None:
    plan = Plan()
    for index in range(count):
        plan.add(_spec(f"N-{index}"))
    for index in range(1, count):
        plan.depend(f"N-{index}", f"N-{index - 1}")
    order = plan.kahn()
    assert order is not None
    assert len(order) == count
    assert plan.cycle_path() is None


class PlanMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.plan = Plan()
        self.at = AT
        self.plan.add(_spec("N-1"))

    @rule(tab=st.sampled_from(("A", "B")))
    def claim(self, tab: str) -> None:
        self.at += timedelta(seconds=1)
        try:
            self.plan.claim("N-1", tab_id=tab, at=self.at)
        except DomainError:
            return

    @rule(fence=st.integers(min_value=0, max_value=4))
    def beat(self, fence: int) -> None:
        node = self.plan.get("N-1")
        if node.lease_expires_at is not None and self.at < node.lease_expires_at and fence != node.fence:
            with pytest.raises(DomainError, match="LEASE_STALE_FENCE"):
                self.plan.heartbeat("N-1", fence=fence, at=self.at)
            return
        try:
            self.plan.heartbeat("N-1", fence=fence, at=self.at)
        except DomainError:
            return

    @rule()
    def lapse(self) -> None:
        self.at += timedelta(minutes=16)
        self.plan.expire_leases(self.at)

    @invariant()
    def one_holder_and_ready_matches(self) -> None:
        node = self.plan.get("N-1")
        assert node.tab_id in {None, "A", "B"}
        expected = node.status is NodeStatus.PENDING and not self.plan.unmet_blockers("N-1")
        assert self.plan.ready("N-1") is expected


TestPlanMachine = PlanMachine.TestCase
TestPlanMachine.settings = settings(max_examples=40, stateful_step_count=12, deadline=None)
