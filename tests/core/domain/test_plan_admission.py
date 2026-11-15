"""Hierarchical admission rejects broadening scopes atomically."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.plan import Move, NodeSpec, NodeStatus, Plan, Role
from aconsole.core.domain.plan_admission import AppliedChange, PlanTopology, TopologyContract, scope_contains

AT = datetime(2026, 11, 15, tzinfo=UTC)
DIRECTION = NodeSpec(
    key="CORE-0",
    created_at=AT,
    level=0,
    paths=("src/**",),
    invariants="Pure domain",
    gate_profile={"tier3": ["pytest"]},
)
TASK = NodeSpec(key="CORE-1", created_at=AT, parent="CORE-0", pipeline=True, paths=("src/domain/*.py",))


@pytest.mark.parametrize(
    ("scope", "paths", "expected"),
    [
        (("src/**",), ("src/domain/*.py",), True),
        (("src/**",), ("src/*",), True),
        (("src/?*",), ("src/*",), True),
        (("src/**",), ("src",), True),
        (("src/**/a.py",), ("src/a.py", "src/x/y/a.py"), True),
        (("src/a.py",), ("src/*.py",), False),
        (("src/a*",), ("src/b*",), False),
        (("src/[ab].py",), ("src/?.py",), False),
        (("src/[a-z].py",), ("src/[bc].py",), True),
        (("src/a.py", "src/b.py"), ("src/[ab].py",), True),
        (("src/*",), ("src/**",), False),
        (("src/**",), ("tests/a.py",), False),
        (("src/**",), ("src/../tests/a.py",), False),
        (("**",), ("/etc/passwd",), False),
        (("src/**",), ("src//a.py",), False),
        (("**",), ("src/é.py",), True),
        ((), ("src/a.py",), False),
    ],
)
def test_scope_language_containment(scope: tuple[str, ...], paths: tuple[str, ...], *, expected: bool) -> None:
    assert scope_contains(scope, paths) is expected


def test_direction_contract_and_forward_references() -> None:
    plan = Plan()
    plan.add_many((TASK, replace(DIRECTION, depends_on=(TASK.key,))))
    assert plan.get(DIRECTION.key).invariants == DIRECTION.invariants
    assert plan.get(DIRECTION.key).gate_profile == DIRECTION.gate_profile
    assert not plan.ready(DIRECTION.key)
    assert plan.ready(TASK.key)
    assert len(plan.admission_warnings()) == 2


@pytest.mark.parametrize(
    "task",
    [
        replace(TASK, parent=None),
        replace(TASK, paths=("tests/a.py",)),
        replace(TASK, paths=("**",)),
        replace(TASK, paths=()),
        replace(TASK, level=2),
        replace(TASK, invariants="only L0"),
        replace(TASK, gate_profile={}),
    ],
)
def test_invalid_contract_rolls_back_batch(task: NodeSpec) -> None:
    plan = Plan()
    with pytest.raises(DomainError, match="NODE_CONTRACT") as caught:
        plan.add_many((DIRECTION, task))
    assert caught.value.code == 4013
    assert plan.kahn() == ()


def test_pipeline_parent_must_be_direction() -> None:
    plan = Plan()
    with pytest.raises(DomainError, match="NODE_CONTRACT"):
        plan.add_many((replace(DIRECTION, level=1, invariants=None, gate_profile=None), TASK))


def test_edit_cannot_expand_child_or_shrink_parent_scope() -> None:
    plan = Plan()
    plan.add_many((DIRECTION, TASK))
    for key, paths in ((TASK.key, ("tests/**",)), (DIRECTION.key, ("src/other/**",))):
        original = plan.get(key)
        with pytest.raises(DomainError, match="NODE_CONTRACT"):
            plan.edit(key, paths=paths, review_required=False)
        assert plan.get(key) == original


def test_legacy_parent_and_empty_scope_remain_valid() -> None:
    plan = Plan()
    plan.add_many((NodeSpec(key="old", created_at=AT), NodeSpec(key="child", parent="old", created_at=AT)))
    assert plan.ready("child")


def _topology(children: int = 6) -> PlanTopology:
    return PlanTopology().add(
        (
            TopologyContract(
                key="CORE-0",
                objective="Direction",
                level=0,
                paths=("src/**",),
                invariants="Pure domain",
                gate_profile_json='{"tier3": ["pytest"]}',
            ),
            *(
                TopologyContract(
                    key=f"CORE-{number}",
                    objective="Work",
                    parent="CORE-0",
                    paths=(f"src/{number}.py",),
                    acceptance=("works",),
                    test_cmd="pytest",
                )
                for number in range(1, children + 1)
            ),
        )
    )


def _change(number: int, *, parent: str = "CORE-0", kind: str = "add") -> AppliedChange:
    # The Literal field is selected explicitly, keeping this helper's callers concise.
    return AppliedChange(
        id=f"change-{number}",
        kind="split" if kind == "split" else "add",
        status="applied",
        decided_by="human",
        nodes=(TopologyContract(key=f"NEW-{number}", objective="Added", parent=parent),),
    )


def test_freeze_accepts_six_baseline_children_and_is_idempotent() -> None:
    draft = _topology()
    frozen = draft.freeze(at=AT, decided_by="human")
    assert draft.phase == "draft"
    assert frozen.phase == "frozen"
    assert frozen.baseline_nodes == frozen.gross_added == 7
    assert dict(frozen.baseline_children)["CORE-0"] == 6
    assert len(frozen.contract_hashes) == 7
    assert frozen.gates_sha256 is not None
    assert frozen.freeze(at=AT, decided_by="human") is frozen
    with pytest.raises(DomainError, match="PLAN_FROZEN") as caught:
        frozen.add(_change(1).nodes)
    assert caught.value.code == 4011


def test_frozen_hashes_ignore_node_order_and_profile_json_formatting() -> None:
    draft = _topology()
    reordered = replace(
        draft,
        nodes=(*reversed(draft.nodes[1:]), replace(draft.nodes[0], gate_profile_json=' { "tier3" : [ "pytest" ] } ')),
    )
    first = draft.freeze(at=AT, decided_by="human")
    second = reordered.freeze(at=AT, decided_by="human")
    assert first.contract_hashes == second.contract_hashes
    assert first.gates_sha256 == second.gates_sha256
    assert first.baseline_children == second.baseline_children


@pytest.mark.parametrize("field", ["objective", "acceptance", "paths", "test_cmd", "boundaries", "output_format"])
def test_every_contract_field_changes_its_hash(field: str) -> None:
    draft = _topology()
    value = ("changed",) if field in {"acceptance", "paths"} else "changed"
    changed = replace(draft, nodes=(draft.nodes[0], replace(draft.nodes[1], **{field: value}), *draft.nodes[2:]))
    original = dict(draft.freeze(at=AT, decided_by="human").contract_hashes)
    updated = dict(changed.freeze(at=AT, decided_by="human").contract_hashes)
    assert original["CORE-1"] != updated["CORE-1"]
    assert original["CORE-2"] == updated["CORE-2"]


@pytest.mark.parametrize("gates", [False, True])
def test_direction_changes_invalidate_all_child_hashes(*, gates: bool) -> None:
    draft = _topology()
    root = (
        replace(draft.nodes[0], gate_profile_json='{"tier3": ["pytest", "ruff"]}')
        if gates
        else replace(draft.nodes[0], invariants="Changed invariant")
    )
    original = draft.freeze(at=AT, decided_by="human")
    changed = replace(draft, nodes=(root, *draft.nodes[1:])).freeze(at=AT, decided_by="human")
    assert all(dict(original.contract_hashes)[key] != digest for key, digest in changed.contract_hashes)
    assert (original.gates_sha256 != changed.gates_sha256) is gates


def test_growth_counts_cancelled_nodes_and_uses_integer_twenty_percent_limit() -> None:
    frozen = _topology().freeze(at=AT, decided_by="human")
    once = frozen.apply_change(_change(1))
    graph = Plan()
    graph.add(NodeSpec(key="NEW-1", created_at=AT))
    graph.transition(Move(key="NEW-1", to=NodeStatus.CANCELLED, role=Role.HUMAN, at=AT))
    assert once.gross_added == 8  # Cancellation is a status edit, never a topology deletion.
    with pytest.raises(DomainError, match="PLAN_GROWTH_LIMIT"):
        once.apply_change(_change(2))  # 9 > 7 * 1.2, even after cancellation.
    assert once.gross_added == 8
    assert len(once.nodes) == 8


def test_child_growth_uses_baseline_and_rejects_third_addition() -> None:
    frozen = _topology(20).freeze(at=AT, decided_by="human")
    twice = frozen.apply_change(_change(1)).apply_change(_change(2))
    assert dict(twice.baseline_children)["CORE-0"] == 20
    assert twice.gross_added == 23
    with pytest.raises(DomainError, match="PLAN_GROWTH_LIMIT"):
        twice.apply_change(_change(3))  # Total allowance permits it; child allowance does not.


def test_parent_depth_is_limited_and_cycles_are_rejected_atomically() -> None:
    draft = _topology()
    depth_three = draft.add(_change(1, parent="CORE-1").nodes)
    with pytest.raises(DomainError, match="PLAN_GROWTH_LIMIT"):
        depth_three.add(_change(2, parent="NEW-1").nodes)
    with pytest.raises(DomainError, match="PLAN_CYCLE"):
        draft.add((TopologyContract(key="CYCLE", objective="Cycle", parent="CYCLE"),))
    assert len(draft.nodes) == 7


@pytest.mark.parametrize("status", ["open", "rejected"])
def test_only_applied_human_change_can_insert(status: str) -> None:
    frozen = _topology().freeze(at=AT, decided_by="human")
    request = _change(1)
    with pytest.raises(DomainError, match="PLAN_FROZEN"):
        frozen.apply_change(replace(request, status="open" if status == "open" else "rejected"))
    with pytest.raises(DomainError, match="PLAN_ROLE"):
        frozen.apply_change(replace(request, decided_by="codex"))
    assert len(frozen.nodes) == 7


def test_split_requires_receipt_and_change_cannot_be_replayed() -> None:
    frozen = _topology().freeze(at=AT, decided_by="human")
    split = _change(1, kind="split")
    with pytest.raises(DomainError, match="SPLIT_NO_PROOF") as caught:
        frozen.apply_change(split)
    assert caught.value.code == 4019
    approved = replace(split, proof_receipt="a" * 64)
    updated = frozen.apply_change(approved)
    assert len(updated.nodes) == 8
    with pytest.raises(DomainError, match="PLAN_FROZEN"):
        updated.apply_change(approved)


def test_backlog_does_not_create_vertices_or_consume_growth() -> None:
    frozen = _topology().freeze(at=AT, decided_by="human")
    backlog = AppliedChange(id="later", kind="backlog", status="applied", decided_by="human")
    assert frozen.apply_change(backlog) is frozen
    with pytest.raises(DomainError, match="NODE_CONTRACT"):
        frozen.apply_change(replace(backlog, nodes=_change(1).nodes))


def test_phase_changes_require_human_and_completed_plan_rejects_insertions() -> None:
    draft = _topology()
    with pytest.raises(DomainError, match="PLAN_ROLE"):
        draft.freeze(at=AT, decided_by="codex")
    with pytest.raises(DomainError, match="PLAN_FROZEN"):
        draft.complete(decided_by="human")
    frozen = draft.freeze(at=AT, decided_by="human")
    with pytest.raises(DomainError, match="PLAN_ROLE"):
        frozen.complete(decided_by="codex")
    completed = frozen.complete(decided_by="human")
    assert completed.phase == "completed"
    with pytest.raises(DomainError, match="PLAN_FROZEN"):
        completed.apply_change(_change(1))
