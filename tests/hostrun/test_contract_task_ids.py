"""Held hostrun ID tests; move into the active suite only after root approval."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.hostrun import actions


@pytest.mark.parametrize("task", ["C1", "C2", "C99"])
def test_gate_plan_accepts_contract_request_task_id_syntax(tmp_path: Path, task: str) -> None:
    plan = actions.build_plan("gate", [task], tmp_path, str(tmp_path), 30)

    assert plan.gate_id == task
    assert plan.steps[0].argv[-1] == task


@pytest.mark.parametrize("task", ["C", "C0.1", "C1a", "C100", "c1", "CC1", "C-1"])
def test_gate_plan_rejects_malformed_contract_task_ids(tmp_path: Path, task: str) -> None:
    with pytest.raises(actions.ActionError):
        _ = actions.build_plan("gate", [task], tmp_path, str(tmp_path), 30)


def test_gate_plan_preserves_existing_core_task_ids(tmp_path: Path) -> None:
    for task in ("A1.1", "A9.4", "H1", "R0b", "B5a"):
        plan = actions.build_plan("gate", [task], tmp_path, str(tmp_path), 30)
        assert plan.gate_id == task


def test_audit_plan_has_no_contract_task_id(tmp_path: Path) -> None:
    plan = actions.build_plan("gate", ["--audit", "core"], tmp_path, str(tmp_path), 30)

    assert plan.gate_id == ""
    assert plan.steps[0].argv[-2:] == ("--audit", "core")
