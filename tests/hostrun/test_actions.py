"""H-DAG keys reach both hostrun and its stop hook without weakening task parsing."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
from tools.gate.stop_gate import current_task
from tools.hostrun.actions import ActionError, build_plan


@pytest.mark.parametrize("key", ["CORE-7", "DB-5", "SEC-2", "QA-3", "APR-1", "FE-2"])
def test_hdag_gate_key(tmp_path: Path, key: str) -> None:
    plan = build_plan("gate", [key], tmp_path, str(tmp_path), 30)
    assert plan.gate_id == key
    assert plan.steps[-1].argv[-1] == key
    marker = tmp_path / ".aconsole-dev" / "current-task"
    marker.parent.mkdir()
    marker.write_text(key + "\n")
    assert current_task(tmp_path) == key


@pytest.mark.parametrize("key", ["CORE-7;id", "../CORE-7", "core-7", "CORE-", "CORE-10000", "C-1"])
def test_hdag_invalid_key(tmp_path: Path, key: str) -> None:
    with pytest.raises(ActionError):
        _ = build_plan("gate", [key], tmp_path, str(tmp_path), 30)
