"""Grok hook guards: path protection, shell denials and the stop gate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from tools.gate import protect_paths, shell_guard

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "tools" / "gate"


def _hook(script: str, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE / script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )


def _protect(root: Path, path: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return _hook(
        "protect_paths.py",
        {
            "workspaceRoot": str(root),
            "cwd": str(cwd or root),
            "toolName": "write",
            "toolInput": {"file_path": path},
        },
    )


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "src" / "aconsole" / "core").mkdir(parents=True)
    return root


def test_shell_guard_denies_network_git_pip_and_rm() -> None:
    assert shell_guard.decision("curl https://example.com") is not None
    assert shell_guard.decision("git push origin main") is not None
    assert shell_guard.decision("pip install requests") is not None
    assert shell_guard.decision("rm -rf /tmp/elsewhere") is not None
    assert shell_guard.decision("rm -rf .aconsole-dev/gates") is None
    assert shell_guard.decision("git status") is None


def test_protect_paths_allows_progress_only() -> None:
    assert protect_paths.decision("tools/gate/phase_gate.py") is not None
    assert protect_paths.decision(".cursor/hooks.json") is not None
    assert protect_paths.decision("docs/plan/CORE.md") is not None
    assert protect_paths.decision("docs/plan/core/progress/A0.2.md") is None
    assert protect_paths.decision("src/aconsole/__init__.py") is None


def test_protect_paths_relative_allowed_path(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    result = _protect(root, "src/aconsole/core/x.py")
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"decision": "allow"}


def test_protect_paths_resolves_relative_to_cwd(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    result = _protect(root, "aconsole/core/x.py", cwd=root / "src")
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"decision": "allow"}


def test_protect_paths_progress_file_is_allowed(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    result = _protect(root, "docs/plan/core/progress/A2.6.md")
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"decision": "allow"}


def test_protect_paths_plan_file_is_denied(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    result = _protect(root, "docs/plan/CORE.md")
    assert result.returncode == 2
    assert '"decision": "deny"' in result.stdout
    assert "protected path: docs/plan/" in result.stdout


def test_protect_paths_hostrun_source_is_denied(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    result = _protect(root, "tools/hostrun/server.py")
    assert result.returncode == 2
    assert '"decision": "deny"' in result.stdout


def test_protect_paths_dotdot_outside_root_is_denied(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    result = _protect(root, "../outside")
    assert result.returncode == 2
    assert '"decision": "deny"' in result.stdout
    assert "path outside the worktree: ../outside" in result.stdout


def test_protect_paths_absolute_path_outside_root_is_denied(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    result = _protect(root, str(tmp_path / "elsewhere.py"))
    assert result.returncode == 2
    assert "path outside the worktree" in result.stdout


def test_protect_paths_symlink_pointing_outside_is_denied(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    result = _protect(root, "link/file.py")
    assert result.returncode == 2
    assert "path outside the worktree" in result.stdout


def test_protect_paths_ui_views_are_denied(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    result = _protect(root, "src/aconsole/ui/views/x.py")
    assert result.returncode == 2
    assert "protected path: src/aconsole/ui/views/" in result.stdout


def test_protect_paths_ui_client_is_allowed(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    result = _protect(root, "src/aconsole/ui/client/x.py")
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"decision": "allow"}


def test_protect_paths_rejects_non_object_input() -> None:
    completed = subprocess.run(
        [sys.executable, str(GATE / "protect_paths.py")],
        input="[1, 2]",
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert '"decision": "deny"' in completed.stdout


@pytest.mark.parametrize(
    "command",
    [
        "git merge feature",
        "git checkout main",
        "git switch main",
        "git worktree add ../x",
        "git worktree remove ../x",
    ],
)
def test_shell_guard_denies_merges_and_worktree_changes(command: str) -> None:
    assert shell_guard.decision(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "git rebase main",
        "git reset --hard HEAD~1",
        "git commit --amend -m x",
        "git push --force origin x",
        "git filter-branch --all",
        "git update-ref refs/heads/x HEAD",
    ],
)
def test_shell_guard_denies_history_rewrites(command: str) -> None:
    assert shell_guard.decision(command) is not None


@pytest.mark.parametrize("command", ["uv pip install requests", "uv add requests", "uv sync", "pip3 install x"])
def test_shell_guard_denies_package_installs(command: str) -> None:
    reason = shell_guard.decision(command)
    assert reason is not None
    assert "hostrun deps-sync" in reason


@pytest.mark.parametrize(
    "command",
    [
        "python tools/gate/phase_gate.py A2.6",
        ".venv-core/bin/python tools/gate/phase_gate.py A2.6",
    ],
)
def test_shell_guard_denies_direct_gate(command: str) -> None:
    assert shell_guard.decision(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "cat ~/.config/aconsole-hostrun/policy.toml",
        "ls $HOME/.grok/settings.json",
        "cat /home/sas/.cursor/hooks.json",
    ],
)
def test_shell_guard_denies_home_configuration(command: str) -> None:
    assert shell_guard.decision(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "echo x > tools/gate/diff_guard.py",
        "tee docs/plan/CORE.md",
        "sed -i s/a/b/ tools/check.sh",
        "mv x tools/hostrun/server.py",
        "cp a docs/plan/core/bases.json",
        "rm tools/gate/x.py",
        "truncate -s 0 .grok/settings.json",
    ],
)
def test_shell_guard_denies_writes_to_protected_paths(command: str) -> None:
    reason = shell_guard.decision(command)
    assert reason is not None
    assert "protected path in a writing command" in reason


@pytest.mark.parametrize(
    "command",
    [
        "hostrun gate A2.6",
        "git status",
        "git commit -m x",
        "cat tools/gate/diff_guard.py",
    ],
)
def test_shell_guard_allows_routine_commands(command: str) -> None:
    assert shell_guard.decision(command) is None


def test_shell_guard_main_denies_with_json() -> None:
    denied = _hook("shell_guard.py", {"toolName": "run", "toolInput": {"command": "git rebase main"}})
    assert denied.returncode == 2
    assert '"decision": "deny"' in denied.stdout
    allowed = _hook("shell_guard.py", {"toolName": "run", "toolInput": {"command": "git status"}})
    assert allowed.returncode == 0
    assert json.loads(allowed.stdout) == {"decision": "allow"}


def _task_workspace(tmp_path: Path, task: str | None = "A2.6", status: str | None = None) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    if task is not None:
        marker = root / ".aconsole-dev" / "current-task"
        marker.parent.mkdir(parents=True)
        _ = marker.write_text(f"{task}\n{'a' * 40}\n", encoding="utf-8")
    if status is not None:
        progress = root / "docs" / "plan" / "core" / "progress" / "A2.6.md"
        progress.parent.mkdir(parents=True)
        _ = progress.write_text(f"Status: {status}\n", encoding="utf-8")
    return root


def _fake_hostrun(bin_dir: Path, exit_code: int, output: str = "") -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "hostrun"
    _ = script.write_text(f"#!/bin/sh\necho '{output}'\nexit {exit_code}\n", encoding="utf-8")
    script.chmod(0o755)


def _stop(root: Path, path_dir: Path, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    body: dict[str, object] = {"workspaceRoot": str(root), **payload}
    return subprocess.run(
        [sys.executable, str(GATE / "stop_gate.py")],
        input=json.dumps(body),
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": str(path_dir)},
    )


def test_stop_non_end_turn_reason_is_allowed(tmp_path: Path) -> None:
    root = _task_workspace(tmp_path)
    bin_dir = tmp_path / "bin"
    _fake_hostrun(bin_dir, exit_code=1, output="ruff failed")
    result = _stop(root, bin_dir, {"reason": "max_tokens"})
    assert result.returncode == 0
    assert "block" not in result.stdout


def test_stop_without_current_task_is_allowed(tmp_path: Path) -> None:
    root = _task_workspace(tmp_path, task=None)
    bin_dir = tmp_path / "bin"
    _fake_hostrun(bin_dir, exit_code=1, output="ruff failed")
    result = _stop(root, bin_dir, {"reason": "end_turn"})
    assert result.returncode == 0
    assert "block" not in result.stdout


def test_stop_blocked_progress_is_allowed(tmp_path: Path) -> None:
    root = _task_workspace(tmp_path, status="blocked: the sandbox has no podman")
    bin_dir = tmp_path / "bin"
    _fake_hostrun(bin_dir, exit_code=1, output="ruff failed")
    result = _stop(root, bin_dir, {"reason": "end_turn"})
    assert result.returncode == 0
    assert "block" not in result.stdout


def test_stop_failing_gate_blocks_with_task_id(tmp_path: Path) -> None:
    root = _task_workspace(tmp_path)
    bin_dir = tmp_path / "bin"
    _fake_hostrun(bin_dir, exit_code=1, output="ruff: 3 errors")
    result = _stop(root, bin_dir, {"reason": "end_turn"})
    assert result.returncode == 0
    assert '"decision": "block"' in result.stdout
    assert "`hostrun gate A2.6` exited 1" in result.stdout
    assert "ruff: 3 errors" in result.stdout


def test_stop_passing_gate_is_allowed(tmp_path: Path) -> None:
    root = _task_workspace(tmp_path)
    bin_dir = tmp_path / "bin"
    _fake_hostrun(bin_dir, exit_code=0, output="gate ok")
    result = _stop(root, bin_dir, {"reason": "end_turn"})
    assert result.returncode == 0
    assert "block" not in result.stdout


def test_stop_without_hostrun_blocks(tmp_path: Path) -> None:
    root = _task_workspace(tmp_path)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    result = _stop(root, empty_bin, {"reason": "end_turn"})
    assert result.returncode == 0
    assert '"decision": "block"' in result.stdout
    assert "hostrun is not on PATH" in result.stdout


@pytest.mark.parametrize(
    "command",
    [
        "git -C /tmp/x push origin main",
        "git -C /tmp/x reset --hard",
        "git --git-dir=/x/.git push",
        "rm -r -f ~/Projects/x",
        "rm --recursive --force src/x",
        "rm -R src",
        "/usr/bin/curl https://example.com",
        "python3 tools/gate/phase_gate.py A1",
        "./tools/gate/phase_gate.py A1",
        "echo x > tools/gate/a",
    ],
)
def test_shell_guard_denies_regression_cases(command: str) -> None:
    assert shell_guard.decision(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "cat tools/gate/diff_guard.py 2>/dev/null",
        "cat tools/gate/phase_gate.py",
        "ls 2>&1 | head",
        "git -c core.pager=cat log",
        "rm -f x.txt",
        "rm -rf .aconsole-dev/tmp",
        "cat curl.txt",
    ],
)
def test_shell_guard_allows_regression_cases(command: str) -> None:
    assert shell_guard.decision(command) is None


def _raw_hook(script: str, stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE / script)],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def test_protect_paths_nul_byte_in_path_is_denied(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    result = _hook(
        "protect_paths.py",
        {
            "workspaceRoot": str(root),
            "cwd": str(root),
            "toolName": "write",
            "toolInput": {"file_path": "src/aconsole/core/x\u0000.py"},
        },
    )
    assert result.returncode == 2
    assert '"decision": "deny"' in result.stdout


def test_protect_paths_not_json_stdin_is_denied() -> None:
    result = _raw_hook("protect_paths.py", "{")
    assert result.returncode == 2
    assert '"decision": "deny"' in result.stdout


def test_shell_guard_not_json_stdin_is_denied() -> None:
    result = _raw_hook("shell_guard.py", "{")
    assert result.returncode == 2
    assert '"decision": "deny"' in result.stdout


@pytest.mark.parametrize(
    "command",
    [
        "git checkout dev/core",
        "git switch dev/front",
        "git -C . checkout main",
        "git checkout -- tools/gate/diff_guard.py",
        "git restore tools/gate/x.py",
        "python3 -c \"open('tools/gate/x.py','w')\"",
        "perl -e 'open(F,\">.grok/x\")'",
    ],
)
def test_shell_guard_denies_branch_moves_and_inline_writes(command: str) -> None:
    assert shell_guard.decision(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git diff",
        "python3 -m pytest tests/core",
        'python3 -c "print(1)"',
    ],
)
def test_shell_guard_allows_reads_and_inline_prints(command: str) -> None:
    assert shell_guard.decision(command) is None
