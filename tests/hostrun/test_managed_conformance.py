"""The managed MCP conformance selector is a fixed hostrun operation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "hostrun"))

from tools.hostrun import actions  # noqa: E402


def test_conformance_managed_builds_fixed_devtools_runner_argv(tmp_path: Path) -> None:
    repo = str(tmp_path)

    plan = actions.build_plan("conformance", ["managed"], tmp_path, repo, 600)

    assert plan.mode == "exec"
    assert plan.steps[0].argv == ("podman", "start", "devtools")
    step = plan.steps[1]
    assert step.argv == (
        "podman",
        "exec",
        "--workdir",
        repo,
        "devtools",
        f"{repo}/.venv-core/bin/python",
        "-m",
        "aconsole.devcli.mcp_conformance",
    )
    assert ("PYTHON_GIL", "0") in step.env


@pytest.mark.parametrize("args", [[], ["managed", "extra"], ["unknown"], ["server"]])
def test_conformance_managed_rejects_unsupported_argument_shapes(
    tmp_path: Path,
    args: list[str],
) -> None:
    with pytest.raises(actions.ActionError) as raised:
        _ = actions.build_plan("conformance", args, tmp_path, str(tmp_path), 600)

    assert raised.value.status == 400
