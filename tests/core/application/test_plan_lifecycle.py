"""Frozen contracts stay immutable across every mutation adapter."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from aconsole.core.application.ports.plan_store import StoredPlan, StoredPlanChange
from aconsole.core.application.services.plan_lifecycle import apply_plan_change, freeze_plan
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.mcp.tools import NodeIn, PlanBoard, TabContext, _ToolError
from tests.gate_profiles import MICRO_PROFILE

_AT = datetime(2026, 11, 16, tzinfo=UTC)
_CTX = TabContext(tab_id="t", project_id="p", worktree=None, agent="codex")


def _draft() -> tuple[PlanBoard, StoredPlan]:
    board = PlanBoard(now=lambda: _AT)
    nodes = [
        NodeIn(
            key="D-0",
            title="Direction",
            objective="Domain",
            level=0,
            paths=["src/**"],
            acceptance=["green"],
            invariants="pure",
            gate_profile=MICRO_PROFILE,
        )
    ]
    nodes.extend(
        NodeIn(
            key=f"C-{i}",
            title=f"Module {i}",
            objective="Check",
            paths=[f"src/{i}.py"],
            acceptance=["green"],
            test_cmd="pytest",
            pipeline=True,
            parent="D-0",
        )
        for i in range(1, 5)
    )
    created = board.create(_CTX, "Plan", "Goal", nodes)
    return board, board.export_plan(created.plan_id)


def _radius(plan: StoredPlan) -> tuple[dict[str, tuple[str, ...] | None], bool]:
    return {node.key: () for node in plan.nodes}, True


def test_present_graph_requires_explicit_radius_before_freeze() -> None:
    _board, plan = _draft()
    with pytest.raises(DomainError, match="NODE_CONTRACT"):
        freeze_plan(plan, {}, at=_AT, graph_present=True)
    assert plan.topology is not None
    assert plan.topology.phase == "draft"
    assert all(node.contract_sha256 is None for node in plan.nodes)


def test_frozen_graph_and_vault_edits_cannot_replace_contract() -> None:
    board, plan = _draft()
    board.freeze(plan.id, _radius(plan)[0], graph_present=True)
    frozen = board.export_plan(plan.id)
    with pytest.raises(_ToolError, match="PLAN_FROZEN"):
        board.add_nodes(_CTX, plan.id, [NodeIn(key="C-5", title="Extra", objective="extra")])
    note = next(node.note for node in board.node_notes(plan.id) if node.key == "C-1")
    with pytest.raises(DomainError, match="PLAN_FROZEN"):
        board.apply_note(plan.id, "C-1", replace(note, objective="changed"))
    assert board.export_plan(plan.id) == frozen


def test_human_change_keeps_baseline_and_cancel_does_not_restore_budget() -> None:
    _board, plan = _draft()
    frozen = freeze_plan(plan, _radius(plan)[0], at=_AT, graph_present=True)
    extra = replace(frozen.nodes[1], id="new", key="C-5")
    change = StoredPlanChange(id="change", plan_id=plan.id, kind="add", status="open", payload={})
    inserted = replace(frozen, nodes=(*frozen.nodes, extra))
    applied = apply_plan_change(frozen, inserted, change, (extra,), proof_receipt=None, radius=_radius)
    assert applied.topology is not None
    assert applied.topology.baseline_nodes == 5
    assert applied.topology.gross_added == 6
    assert applied.nodes[1].contract_sha256 == frozen.nodes[1].contract_sha256
    cancelled = replace(applied, nodes=tuple(replace(node, status="cancelled") for node in applied.nodes))
    another = replace(extra, id="another", key="C-6")
    with pytest.raises(DomainError, match="PLAN_GROWTH_LIMIT"):
        apply_plan_change(
            cancelled,
            replace(cancelled, nodes=(*cancelled.nodes, another)),
            replace(change, id="second"),
            (another,),
            proof_receipt=None,
            radius=_radius,
        )


def test_split_requires_kernel_resolved_failure_receipt() -> None:
    _board, plan = _draft()
    frozen = freeze_plan(plan, _radius(plan)[0], at=_AT, graph_present=True)
    change = StoredPlanChange(
        id="split", plan_id=plan.id, kind="split", status="open", payload={"proof_receipt": "agent-forged-value"}
    )
    with pytest.raises(DomainError, match="SPLIT_NO_PROOF"):
        apply_plan_change(frozen, frozen, change, (), proof_receipt=None, radius=_radius)
