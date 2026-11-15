"""Hierarchical admission rejects broadening scopes atomically."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.plan import NodeSpec, Plan
from aconsole.core.domain.plan_admission import scope_contains

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
