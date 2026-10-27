"""diff_guard on a temporary repository."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from tools.gate import diff_guard

ROOT = Path(__file__).resolve().parents[2]


def _git(repo: Path, *args: str) -> None:
    _ = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=gate@example.com", "-c", "user.name=gate", *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _guard(repo: Path, base: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "gate" / "diff_guard.py"),
            "--repo",
            str(repo),
            "--base",
            base,
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _init(tmp: Path) -> str:
    _git(tmp, "init")
    (tmp / "tests").mkdir()
    test = tmp / "tests" / "test_sample.py"
    _ = test.write_text(
        "def test_ok() -> None:\n    value = 1\n    assert value == 1\n    assert value == 2\n",
        encoding="utf-8",
    )
    _git(tmp, "add", ".")
    _git(tmp, "commit", "-m", "base")
    return subprocess.run(
        ["git", "-C", str(tmp), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_clean_change_passes(tmp_path: Path) -> None:
    base = _init(tmp_path)
    _ = (tmp_path / "src.txt").write_text("ok\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "note")
    result = _guard(tmp_path, base)
    assert result.returncode == 0


def test_removed_test_fails(tmp_path: Path) -> None:
    base = _init(tmp_path)
    text = (tmp_path / "tests" / "test_sample.py").read_text(encoding="utf-8")
    _ = (tmp_path / "tests" / "test_sample.py").write_text(
        text.replace("    assert value == 2\n", ""), encoding="utf-8"
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "weaken")
    result = _guard(tmp_path, base)
    assert result.returncode != 0
    assert "assert-count-reduced" in result.stdout


def test_skip_fails(tmp_path: Path) -> None:
    base = _init(tmp_path)
    _ = (tmp_path / "tests" / "test_sample.py").write_text(
        "import pytest\n\n@pytest.mark.skip\ndef test_ok() -> None:\n    value = 1\n    assert value == 1\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "skip")
    result = _guard(tmp_path, base)
    assert result.returncode != 0
    assert "skip-added" in result.stdout or "test-removed" in result.stdout


def test_hostrun_sources_are_protected(tmp_path: Path) -> None:
    # Changed from "not protected": every tool is protected since the Grok hooks landed.
    base = _init(tmp_path)
    hostrun = tmp_path / "tools" / "hostrun"
    hostrun.mkdir(parents=True)
    _ = (hostrun / "actions.py").write_text("VALUE = 1\n", encoding="utf-8")
    _ = (tmp_path / "tools" / "check.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "hostrun")
    result = _guard(tmp_path, base)
    assert result.returncode != 0
    assert "tools/hostrun/actions.py" in result.stdout
    assert "tools/check.sh" in result.stdout


def test_protected_tool_fails(tmp_path: Path) -> None:
    base = _init(tmp_path)
    gate = tmp_path / "tools" / "gate"
    gate.mkdir(parents=True)
    _ = (gate / "x.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "gate")
    result = _guard(tmp_path, base)
    assert result.returncode != 0
    assert "protected-file-changed" in result.stdout


def test_uncommitted_weakened_test_fails(tmp_path: Path) -> None:
    base = _init(tmp_path)
    text = (tmp_path / "tests" / "test_sample.py").read_text(encoding="utf-8")
    _ = (tmp_path / "tests" / "test_sample.py").write_text(
        text.replace("    assert value == 2\n", ""), encoding="utf-8"
    )
    result = _guard(tmp_path, base)
    assert result.returncode != 0
    assert "assert-count-reduced" in result.stdout


def test_bases_file_is_protected(tmp_path: Path) -> None:
    # Changed from "not protected": bases.json is a protected plan file.
    base = _init(tmp_path)
    bases = tmp_path / "docs" / "plan" / "core"
    bases.mkdir(parents=True)
    _ = (bases / "bases.json").write_text('{"A0.0r": "' + ("a" * 40) + '"}\n', encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "bases")
    result = _guard(tmp_path, base)
    assert result.returncode != 0
    assert "protected-file-changed" in result.stdout
    assert "docs/plan/core/bases.json" in result.stdout


def test_progress_file_is_not_protected(tmp_path: Path) -> None:
    base = _init(tmp_path)
    progress = tmp_path / "docs" / "plan" / "core" / "progress"
    progress.mkdir(parents=True)
    _ = (progress / "R0b.md").write_text("Status: doing\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "progress")
    result = _guard(tmp_path, base)
    assert result.returncode == 0


def test_other_plan_file_stays_protected(tmp_path: Path) -> None:
    base = _init(tmp_path)
    plan = tmp_path / "docs" / "plan"
    plan.mkdir(parents=True)
    _ = (plan / "CORE.md").write_text("changed\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "plan")
    result = _guard(tmp_path, base)
    assert result.returncode != 0
    assert "protected-file-changed" in result.stdout


def test_untracked_protected_file_fails(tmp_path: Path) -> None:
    base = _init(tmp_path)
    gate = tmp_path / "tools" / "gate"
    gate.mkdir(parents=True)
    _ = (gate / "x.py").write_text("VALUE = 1\n", encoding="utf-8")
    result = _guard(tmp_path, base)
    assert result.returncode != 0
    assert "protected-file-changed" in result.stdout


def test_i0_tools_are_protected(tmp_path: Path) -> None:
    # Changed from "not protected": the I0 tools fall under tools/**.
    base = _init(tmp_path)
    tools = tmp_path / "tools"
    tools.mkdir()
    _ = (tools / "api_floor_check.py").write_text("VALUE = 1\n", encoding="utf-8")
    _ = (tools / "ui_rss.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "i0 tools")
    result = _guard(tmp_path, base)
    assert result.returncode != 0
    assert "tools/api_floor_check.py" in result.stdout
    assert "tools/ui_rss.py" in result.stdout


def test_i0_pyproject_deltas_are_rejected(tmp_path: Path) -> None:
    # Changed from "allowed": the I0 exemption was removed, so any protected table delta fails.
    before = [
        '[tool.basedpyright]\ntypeCheckingMode = "all"\nvenv = ".venv-core"\n',
        'exclude = ["src/aconsole/ui"]\n\n[tool.ruff]\nextend-exclude = ["docs/plan/core/gate_prototype"]\n',
    ]
    _ = (tmp_path / "pyproject.toml").write_text("".join(before), encoding="utf-8")
    base = _init(tmp_path)
    after = [
        '[tool.basedpyright]\ntypeCheckingMode = "all"\nvenv = ".venv-ui"\n',
        'reportMissingModuleSource = "none"\n\n',
        '[tool.ruff]\nextend-exclude = ["tests/fixtures/ui_banned", "docs/plan/core/gate_prototype"]\n',
    ]
    _ = (tmp_path / "pyproject.toml").write_text("".join(after), encoding="utf-8")
    result = _guard(tmp_path, base)
    assert result.returncode != 0
    assert "protected-pyproject-table" in result.stdout
    assert "tool.ruff.extend-exclude" in result.stdout


def test_basedpyright_mode_stays_protected(tmp_path: Path) -> None:
    _ = (tmp_path / "pyproject.toml").write_text(
        '[tool.basedpyright]\ntypeCheckingMode = "all"\n',
        encoding="utf-8",
    )
    base = _init(tmp_path)
    _ = (tmp_path / "pyproject.toml").write_text(
        '[tool.basedpyright]\ntypeCheckingMode = "basic"\n',
        encoding="utf-8",
    )
    result = _guard(tmp_path, base)
    assert result.returncode != 0
    assert "protected-pyproject-table" in result.stdout


@pytest.mark.parametrize(
    "path",
    [
        "tools/hostrun/server.py",
        "tools/check.sh",
        "tools/gate/diff_guard.py",
        "tools/gate/phase_gate.py",
        "tools/api_floor_check.py",
        ".grok/settings.json",
        "AGENTS.md",
        "src/aconsole/core/AGENTS.md",
        "src/aconsole/ui/AGENTS.md",
        "docs/plan/CORE.md",
        "docs/plan/core/bases.json",
        "docs/plan/core/PHASE_PROTOCOL.md",
    ],
)
def test_is_protected_covers_tools_plan_and_grok(path: str) -> None:
    assert diff_guard.is_protected(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "src/aconsole/core/x.py",
        "docs/plan/core/progress/A2.6.md",
    ],
)
def test_is_protected_leaves_core_and_progress_open(path: str) -> None:
    assert diff_guard.is_protected(path) is False


def test_items_counts_asserts_in_a_module_helper() -> None:
    source = (
        "def _check(value: int) -> None:\n"
        "    assert value == 1\n"
        "    assert value != 2\n"
        "\n"
        "def test_uses_helper() -> None:\n"
        "    _check(1)\n"
    )
    items = diff_guard.test_items(source)
    assert items["test_uses_helper"]["asserts"] == 2


def test_items_helper_without_asserts_counts_zero() -> None:
    source = "def _noop() -> None:\n    return None\n\ndef test_calls_noop() -> None:\n    _noop()\n"
    items = diff_guard.test_items(source)
    assert items["test_calls_noop"]["asserts"] == 0


def test_items_counts_pytest_raises() -> None:
    source = "import pytest\n\ndef test_raises() -> None:\n    with pytest.raises(ValueError):\n        int('x')\n"
    items = diff_guard.test_items(source)
    assert items["test_raises"]["asserts"] == 1


def test_items_counts_pytest_warns() -> None:
    source = (
        "import warnings\n"
        "import pytest\n\n"
        "def test_warns() -> None:\n"
        "    with pytest.warns(UserWarning):\n"
        "        warnings.warn('x', UserWarning, stacklevel=1)\n"
    )
    items = diff_guard.test_items(source)
    assert items["test_warns"]["asserts"] == 1


def test_items_counts_class_helper_called_via_self() -> None:
    source = (
        "class TestThing:\n"
        "    def _check(self, value: int) -> None:\n"
        "        assert value == 1\n"
        "\n"
        "    def test_it(self) -> None:\n"
        "        self._check(1)\n"
    )
    items = diff_guard.test_items(source)
    assert items["TestThing::test_it"]["asserts"] == 1


def test_items_counts_helpers_transitively() -> None:
    source = (
        "def _inner() -> None:\n"
        "    assert True is True or False\n"
        "    assert [1] == [1]\n"
        "\n"
        "def _outer() -> None:\n"
        "    _inner()\n"
        "\n"
        "def test_chain() -> None:\n"
        "    _outer()\n"
    )
    items = diff_guard.test_items(source)
    assert items["test_chain"]["asserts"] == 2


def test_items_trivial_asserts_do_not_count() -> None:
    source = "def test_trivial() -> None:\n    assert 1 == 1\n    assert True\n"
    items = diff_guard.test_items(source)
    assert items["test_trivial"]["asserts"] == 0


def test_grok_rules_and_hooks_are_protected(tmp_path: Path) -> None:
    # Round 6 K1: the Grok rules, commands, and hooks are protected like the other tools.
    base = _init(tmp_path)
    rules = tmp_path / ".grok" / "rules"
    rules.mkdir(parents=True)
    _ = (rules / "protocol.md").write_text("changed\n", encoding="utf-8")
    hooks = tmp_path / ".grok" / "hooks"
    hooks.mkdir(parents=True)
    _ = (hooks / "gate.json").write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "grok")
    result = _guard(tmp_path, base)
    assert result.returncode != 0
    assert "protected-file-changed" in result.stdout
    assert ".grok/rules/protocol.md" in result.stdout
    assert ".grok/hooks/gate.json" in result.stdout
