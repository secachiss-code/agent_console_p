"""Host-side, task-scoped review of exact protected-path gate hits."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOSTRUN = str(ROOT / "tools" / "hostrun")
if HOSTRUN not in sys.path:
    sys.path.insert(0, HOSTRUN)

from tools.hostrun import actions, server  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

TASK = "A9.3"
PATH = "tools/gate/shell_guard.py"


def _policy(tmp: Path) -> server.Policy:
    projects = tmp / "Projects"
    repo = projects / "app"
    repo.mkdir(parents=True)
    example = server.load_policy(ROOT / "tools" / "hostrun" / "policy.example.toml")
    containers = {
        name: server.ContainerPolicy(actions=spec.actions, worktree=str(repo))
        for name, spec in example.containers.items()
    }
    return server.Policy(
        projects_root=str(projects),
        repo=str(repo),
        socket_path=str(tmp / "hostrun.sock"),
        audit_path=str(tmp / "audit.jsonl"),
        state_dir=str(tmp / "host-state"),
        output_limit_bytes=example.output_limit_bytes,
        default_timeout_s=example.default_timeout_s,
        timeouts=example.timeouts,
        containers=containers,
    )


def _git(repo: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    return completed.stdout


def _repo_with_tracked_main_edit(tmp: Path, task: str = TASK) -> tuple[server.Policy, Path, str, str, bytes]:
    policy = _policy(tmp)
    repo = Path(policy.repo)
    target = repo / PATH
    target.parent.mkdir(parents=True)
    target.write_bytes(b"before\n")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "hostrun test")
    _git(repo, "config", "user.email", "hostrun-test@example.invalid")
    _git(repo, "add", PATH)
    _git(repo, "commit", "-qm", "task base")
    base = _git(repo, "rev-parse", "HEAD").decode().strip()
    exclude = repo / ".git" / "info" / "exclude"
    exclude.write_text("/docs/plan/core/bases.json\n", encoding="utf-8")
    bases = repo / "docs" / "plan" / "core" / "bases.json"
    bases.parent.mkdir(parents=True)
    bases.write_text(json.dumps({task: base}), encoding="utf-8")
    target.write_bytes(b"after\n")
    diff = _git(repo, "diff", "--binary", base, "--", PATH)
    return policy, repo, base, PATH, diff


def _repo_with_internal_symlink_change(tmp: Path) -> tuple[server.Policy, Path, str, str]:
    policy = _policy(tmp)
    repo = Path(policy.repo)
    gate = repo / "tools" / "gate"
    gate.mkdir(parents=True)
    shell_guard = gate / "shell_guard.py"
    other_guard = gate / "other_guard.py"
    alias = gate / "alias.py"
    shell_guard.write_bytes(b"before\n")
    other_guard.write_bytes(b"other\n")
    alias.symlink_to("shell_guard.py")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "hostrun test")
    _git(repo, "config", "user.email", "hostrun-test@example.invalid")
    _git(repo, "add", "tools/gate/shell_guard.py", "tools/gate/other_guard.py", "tools/gate/alias.py")
    _git(repo, "commit", "-qm", "task base")
    base = _git(repo, "rev-parse", "HEAD").decode().strip()
    exclude = repo / ".git" / "info" / "exclude"
    exclude.write_text("/docs/plan/core/bases.json\n", encoding="utf-8")
    bases = repo / "docs" / "plan" / "core" / "bases.json"
    bases.parent.mkdir(parents=True)
    bases.write_text(json.dumps({TASK: base}), encoding="utf-8")
    alias.unlink()
    alias.symlink_to("other_guard.py")
    shell_guard.write_bytes(b"after\n")
    return policy, repo, base, "tools/gate/alias.py"


def _review_path(policy: server.Policy, task: str, path: str = PATH) -> Path:
    key = hashlib.sha256(path.encode("utf-8")).hexdigest()
    return Path(policy.state_dir) / "gate-reviews" / task / f"{key}.json"


def _run_plan(
    policy: server.Policy,
    plan: actions.Plan,
    *,
    container: str = "agent-claude",
) -> tuple[int, bytes]:
    output: list[bytes] = []
    code = server.Application(policy).run_plan(
        plan,
        cwd=policy.repo,
        write=output.append,
        container=container,
    )
    return code, b"".join(output)


def _review_plan(policy: server.Policy, *args: str) -> actions.Plan:
    return actions.build_plan(
        "gate-review",
        list(args),
        Path(policy.repo),
        policy.repo,
        30,
    )


def _inspect_and_approve(policy: server.Policy, task: str, path: str, diff: bytes) -> dict[str, object]:
    inspect_plan = _review_plan(policy, "inspect", task, path)
    code, output = _run_plan(policy, inspect_plan)
    assert code == 0
    inspection = json.loads(output)
    assert isinstance(inspection, dict)
    assert inspection["task"] == task
    assert inspection["base"]
    assert inspection["path"] == path
    assert inspection["status"] == "M"
    digest = hashlib.sha256(diff).hexdigest()
    assert inspection["digest"] == digest
    assert base64.b64decode(inspection["diff_base64"]) == diff

    approve_plan = _review_plan(policy, "approve", task, path, digest)
    code, output = _run_plan(policy, approve_plan)
    assert code == 0
    record = json.loads(output)
    assert record == {
        "task": task,
        "repo": str(Path(policy.repo).resolve()),
        "base": inspection["base"],
        "path": path,
        "status": "M",
        "digest": digest,
        "recorded": True,
    }
    stored = json.loads(_review_path(policy, task, path).read_text(encoding="utf-8"))
    assert stored == {key: value for key, value in record.items() if key != "recorded"}
    return stored


def _gate_report(
    repo: Path,
    task: str,
    base: str,
    path: str,
    *,
    extra_violations: list[dict[str, object]] | None = None,
    extra_failed_step: bool = False,
) -> bytes:
    head = _git(repo, "rev-parse", "HEAD").decode().strip()
    violations: list[dict[str, object]] = [
        {
            "gate": "diff_guard",
            "rule": "protected-file-changed",
            "path": path,
            "line": None,
            "detail": "status M",
        }
    ]
    violations.extend(extra_violations or [])
    step_names = (
        "no_placeholders",
        "diff_guard",
        "done_when",
        "ruff",
        "basedpyright",
        "check_no_raw_sql",
        "check_no_js",
        "check_contracts",
        "import_linter",
        "pytest-core",
        "pytest-ui",
    )
    steps = []
    for name in step_names:
        fails = name == "diff_guard" or (name == "ruff" and extra_failed_step)
        steps.append(
            {
                "name": name,
                "argv": [],
                "exit_code": int(fails),
                "duration_s": 0.01,
                "status": "fail" if fails else "pass",
                "log": f"{name}.log",
            }
        )
    report: dict[str, object] = {
        "schema_version": 1,
        "phase_id": task,
        "started_at": "2026-10-31T00:00:00+00:00",
        "finished_at": "2026-10-31T00:00:01+00:00",
        "mode": "task",
        "runner": "agent-claude",
        "via_hostrun": True,
        "repo": {"path": str(repo), "base": base, "head": head, "dirty_worktree": True},
        "steps": steps,
        "counts": {
            "placeholder_bypass": 0,
            "guard_violations": sum(1 for item in violations if item["gate"] == "diff_guard"),
            "done_when": 0,
            "ruff": 0,
            "basedpyright": 0,
            "check_no_raw_sql": 0,
            "check_no_js": 0,
            "check_contracts": 0,
            "import_linter": 0,
            "coverage_layers": 0,
        },
        "coverage": {"percent": 91.2, "fail_under": 90, "layers": {}},
        "violations_total": len(violations),
        "violations": violations,
        "verdict": "fail",
        "failed_steps": ["diff_guard"] + (["ruff"] if extra_failed_step else []),
    }
    return (json.dumps(report, indent=2) + "\n").encode("utf-8")


def _run_gate_with_report(
    monkeypatch: pytest.MonkeyPatch,
    policy: server.Policy,
    repo: Path,
    task: str,
    raw: bytes,
) -> tuple[int, bytes, Path]:
    report_path = repo / ".aconsole-dev" / "gates" / f"{task}.json"

    def fake_execute(
        _argv: tuple[str, ...],
        *,
        cwd: str,
        env: dict[str, str],
        deadline: float,
        output_limit: int,
        written: list[int],
        write: Callable[[bytes], None],
    ) -> int:
        del cwd, env, deadline, output_limit, written
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(raw)
        write(b"phase gate reported FAIL\n")
        return 1

    monkeypatch.setattr(server, "execute_argv", fake_execute)
    plan = actions.build_plan("gate", [task], Path(policy.repo), policy.repo, 30)
    code, output = _run_plan(policy, plan)
    return code, output, report_path


def test_gate_review_action_is_allowed_only_for_main() -> None:
    policy = server.load_policy(ROOT / "tools" / "hostrun" / "policy.example.toml")
    assert "gate-review" in actions.KNOWN_ACTIONS
    assert policy.allows("agent-claude", "gate-review")
    assert all(not policy.allows(name, "gate-review") for name in ("agent-grok", "agent-cursor", "agent-codex"))


def test_inspect_and_approve_bind_record_to_exact_task_base_path_status_and_diff(
    tmp_path: Path,
) -> None:
    policy, repo, base, path, diff = _repo_with_tracked_main_edit(tmp_path)

    record = _inspect_and_approve(policy, TASK, path, diff)

    assert record == {
        "task": TASK,
        "repo": str(repo.resolve()),
        "base": base,
        "path": path,
        "status": "M",
        "digest": hashlib.sha256(diff).hexdigest(),
    }
    assert repo.joinpath(path).read_bytes() == b"after\n"


@pytest.mark.parametrize(
    "args",
    [
        ("inspect", TASK),
        ("inspect", TASK, "tools/gate/*.py"),
        ("inspect", TASK, "tests/gate/test_hooks.py"),
        ("inspect", TASK, "tools/gate/missing.py"),
        ("approve", TASK, PATH, "not-a-sha256"),
        ("delete", TASK, PATH),
    ],
)
def test_gate_review_rejects_wrong_arity_globs_untracked_or_bad_digest(
    tmp_path: Path,
    args: tuple[str, ...],
) -> None:
    policy, _repo, _base, _path, _diff = _repo_with_tracked_main_edit(tmp_path)

    with pytest.raises(actions.ActionError):
        _ = _review_plan(policy, *args)


@pytest.mark.parametrize("change", ["untracked", "deleted", "renamed"])
def test_review_inspection_accepts_only_tracked_modifications(
    tmp_path: Path,
    change: str,
) -> None:
    policy, repo, _base, path, _diff = _repo_with_tracked_main_edit(tmp_path)
    if change == "untracked":
        candidate = "tools/gate/new_guard.py"
        target = repo / candidate
        target.write_bytes(b"new file\n")
    elif change == "deleted":
        candidate = path
        (repo / path).unlink()
    else:
        candidate = "tools/gate/renamed_guard.py"
        (repo / path).rename(repo / candidate)

    try:
        plan = _review_plan(policy, "inspect", TASK, candidate)
    except actions.ActionError:
        return
    code, output = _run_plan(policy, plan)

    assert code != 0
    assert b"tracked" in output.lower() or b"status" in output.lower()


def test_review_inspection_rejects_symlink_to_another_in_repo_main_file(tmp_path: Path) -> None:
    policy, _repo, _base, alias = _repo_with_internal_symlink_change(tmp_path)

    try:
        plan = _review_plan(policy, "inspect", TASK, alias)
    except actions.ActionError:
        return
    code, output = _run_plan(policy, plan)

    assert code != 0
    assert b"symlink" in output.lower() or b"regular" in output.lower() or b"tracked" in output.lower()


def test_approve_rejects_a_diff_that_changed_after_inspection(tmp_path: Path) -> None:
    policy, repo, base, path, _diff = _repo_with_tracked_main_edit(tmp_path)
    inspect = _review_plan(policy, "inspect", TASK, path)
    code, output = _run_plan(policy, inspect)
    assert code == 0
    digest = json.loads(output)["digest"]
    (repo / path).write_bytes(b"changed after review\n")

    code, output = _run_plan(policy, _review_plan(policy, "approve", TASK, path, digest))

    assert code != 0
    assert b"digest" in output.lower() or b"changed" in output.lower()
    assert not _review_path(policy, TASK, path).exists()
    assert digest != hashlib.sha256(_git(repo, "diff", "--binary", base, "--", path)).hexdigest()


def test_approved_protected_hit_effectively_passes_and_preserves_raw_report_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy, repo, base, path, diff = _repo_with_tracked_main_edit(tmp_path)
    record = _inspect_and_approve(policy, TASK, path, diff)
    raw = _gate_report(repo, TASK, base, path)

    code, _output, canonical_path = _run_gate_with_report(monkeypatch, policy, repo, TASK, raw)

    assert code == 0
    effective = json.loads(canonical_path.read_text(encoding="utf-8"))
    assert effective["verdict"] == "pass"
    assert effective["failed_steps"] == []
    assert effective["violations"] == []
    guard_step = next(step for step in effective["steps"] if step["name"] == "diff_guard")
    assert guard_step["status"] == "pass"
    assert guard_step["exit_code"] == 0
    raw_path = canonical_path.with_name(f"{TASK}.raw.json")
    assert raw_path.read_bytes() == raw
    review = effective["review"]
    assert review["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert review["raw_report"] == str(raw_path)
    assert review["raw_diff_guard"]["exit_code"] == 1
    assert review["matched_hits"] == json.loads(raw)["violations"]
    assert review["matched_hits"][0] == effective["review"]["approvals"][0]["hit"]
    assert effective["review"]["approvals"][0]["record"] == record


def test_repeated_acceptance_preserves_first_raw_and_content_addresses_later_reports(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy, repo, base, path, diff = _repo_with_tracked_main_edit(tmp_path)
    _ = _inspect_and_approve(policy, TASK, path, diff)
    first_raw = _gate_report(repo, TASK, base, path)
    code, _output, canonical_path = _run_gate_with_report(monkeypatch, policy, repo, TASK, first_raw)
    assert code == 0
    original_raw_path = canonical_path.with_name(f"{TASK}.raw.json")
    assert original_raw_path.read_bytes() == first_raw

    second_report = json.loads(_gate_report(repo, TASK, base, path))
    second_report["finished_at"] = "2026-10-31T00:00:02+00:00"
    second_raw = (json.dumps(second_report, indent=2) + "\n").encode("utf-8")
    code, _output, canonical_path = _run_gate_with_report(monkeypatch, policy, repo, TASK, second_raw)

    assert code == 0
    assert original_raw_path.read_bytes() == first_raw
    effective = json.loads(canonical_path.read_text(encoding="utf-8"))
    raw_sha = hashlib.sha256(second_raw).hexdigest()
    later_raw_path = Path(effective["review"]["raw_report"])
    assert later_raw_path.name == f"{TASK}.raw.{raw_sha}.json"
    assert later_raw_path.read_bytes() == second_raw


def test_approval_cannot_be_reused_from_a_copied_repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy, repo, base, path, diff = _repo_with_tracked_main_edit(tmp_path)
    _ = _inspect_and_approve(policy, TASK, path, diff)
    copied_repo = repo.parent / "copied"
    shutil.copytree(repo, copied_repo)
    copied_policy = replace(policy, repo=str(copied_repo), projects_root=str(copied_repo.parent))
    raw = _gate_report(copied_repo, TASK, base, path)

    code, _output, canonical_path = _run_gate_with_report(
        monkeypatch,
        copied_policy,
        copied_repo,
        TASK,
        raw,
    )

    assert code != 0
    assert canonical_path.read_bytes() == raw
    assert not canonical_path.with_name(f"{TASK}.raw.json").exists()


@pytest.mark.parametrize("corruption", ["malformed", "extra", "missing", "unavailable", "task", "base", "status"])
def test_malformed_extra_unavailable_or_mismatched_review_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    corruption: str,
) -> None:
    policy, repo, base, path, diff = _repo_with_tracked_main_edit(tmp_path)
    _ = _inspect_and_approve(policy, TASK, path, diff)
    approval_path = _review_path(policy, TASK, path)
    record = json.loads(approval_path.read_text(encoding="utf-8"))
    if corruption == "malformed":
        approval_path.write_text("{", encoding="utf-8")
    elif corruption == "extra":
        record["wildcard"] = "tools/**"
    elif corruption == "missing":
        approval_path.unlink()
    elif corruption == "unavailable":
        approval_path.unlink()
        approval_path.mkdir()
    else:
        record[corruption] = {"task": "A9.4", "base": "0" * 40, "status": "A"}[corruption]
    if corruption not in {"malformed", "missing", "unavailable"}:
        approval_path.write_text(json.dumps(record), encoding="utf-8")
    raw = _gate_report(repo, TASK, base, path)

    code, _output, canonical_path = _run_gate_with_report(monkeypatch, policy, repo, TASK, raw)

    assert code != 0
    assert canonical_path.read_bytes() == raw
    assert not canonical_path.with_name(f"{TASK}.raw.json").exists()


def test_changed_reviewed_file_does_not_match_stored_digest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy, repo, base, path, diff = _repo_with_tracked_main_edit(tmp_path)
    _ = _inspect_and_approve(policy, TASK, path, diff)
    (repo / path).write_bytes(b"changed after approval\n")
    raw = _gate_report(repo, TASK, base, path)

    code, _output, canonical_path = _run_gate_with_report(monkeypatch, policy, repo, TASK, raw)

    assert code != 0
    assert canonical_path.read_bytes() == raw


@pytest.mark.parametrize("stale_field", ["base", "head", "phase_id", "mode", "runner", "via_hostrun"])
def test_stale_or_non_task_gate_report_is_not_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stale_field: str,
) -> None:
    policy, repo, base, path, diff = _repo_with_tracked_main_edit(tmp_path)
    _ = _inspect_and_approve(policy, TASK, path, diff)
    original = json.loads(_gate_report(repo, TASK, base, path))
    if stale_field == "base":
        original["repo"]["base"] = "0" * 40
    elif stale_field == "head":
        original["repo"]["head"] = "0" * 40
    elif stale_field == "phase_id":
        original["phase_id"] = "A8.3"
    elif stale_field == "mode":
        original["mode"] = "audit"
    elif stale_field == "runner":
        original["runner"] = "agent-codex"
    else:
        original["via_hostrun"] = False
    raw = (json.dumps(original, indent=2) + "\n").encode("utf-8")

    code, _output, canonical_path = _run_gate_with_report(monkeypatch, policy, repo, TASK, raw)

    assert code != 0
    assert canonical_path.read_bytes() == raw


@pytest.mark.parametrize(
    ("extra_violations", "extra_failed_step"),
    [
        ([{"gate": "coverage_layers", "rule": "below-floor", "path": "domain", "line": None, "detail": "88% < 90%"}], False),
        ([{"gate": "diff_guard", "rule": "test-removed", "path": "tests/test_a.py", "line": None, "detail": "removed"}], False),
        ([{"gate": "diff_guard", "rule": "protected-file-changed", "path": "tools/hostrun/server.py", "line": None, "detail": "status M"}], False),
        ([], True),
    ],
)
def test_review_does_not_override_independent_gate_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    extra_violations: list[dict[str, object]],
    extra_failed_step: bool,
) -> None:
    policy, repo, base, path, diff = _repo_with_tracked_main_edit(tmp_path)
    _ = _inspect_and_approve(policy, TASK, path, diff)
    raw = _gate_report(
        repo,
        TASK,
        base,
        path,
        extra_violations=extra_violations,
        extra_failed_step=extra_failed_step,
    )

    code, _output, canonical_path = _run_gate_with_report(monkeypatch, policy, repo, TASK, raw)

    assert code != 0
    assert canonical_path.read_bytes() == raw


@pytest.mark.parametrize("inconsistency", ["nonzero-count", "coverage-below-floor"])
def test_review_rejects_inconsistent_counts_or_global_coverage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    inconsistency: str,
) -> None:
    policy, repo, base, path, diff = _repo_with_tracked_main_edit(tmp_path)
    _ = _inspect_and_approve(policy, TASK, path, diff)
    report = json.loads(_gate_report(repo, TASK, base, path))
    if inconsistency == "nonzero-count":
        report["counts"]["ruff"] = 1
    else:
        report["coverage"]["percent"] = 89.99
    raw = (json.dumps(report, indent=2) + "\n").encode("utf-8")

    code, _output, canonical_path = _run_gate_with_report(monkeypatch, policy, repo, TASK, raw)

    assert code != 0
    assert canonical_path.read_bytes() == raw


@pytest.mark.parametrize(
    "malformation",
    ["bool-schema", "bool-guard-exit", "bool-violation-count", "bool-guard-count", "bool-pass-exit", "duplicate-field"],
)
def test_review_rejects_boolean_integer_fields_and_duplicate_json_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    malformation: str,
) -> None:
    policy, repo, base, path, diff = _repo_with_tracked_main_edit(tmp_path)
    _ = _inspect_and_approve(policy, TASK, path, diff)
    raw = _gate_report(repo, TASK, base, path)
    if malformation == "duplicate-field":
        raw = raw.replace(b'"schema_version": 1,', b'"schema_version": 1,\n  "schema_version": 1,', 1)
    else:
        report = json.loads(raw)
        if malformation == "bool-schema":
            report["schema_version"] = True
        elif malformation == "bool-guard-exit":
            next(step for step in report["steps"] if step["name"] == "diff_guard")["exit_code"] = True
        elif malformation == "bool-violation-count":
            report["violations_total"] = True
        elif malformation == "bool-guard-count":
            report["counts"]["guard_violations"] = True
        else:
            next(step for step in report["steps"] if step["name"] == "ruff")["exit_code"] = False
        raw = (json.dumps(report, indent=2) + "\n").encode("utf-8")

    code, _output, canonical_path = _run_gate_with_report(monkeypatch, policy, repo, TASK, raw)

    assert code != 0
    assert canonical_path.read_bytes() == raw


def test_audit_gate_is_never_postprocessed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy, repo, base, path, diff = _repo_with_tracked_main_edit(tmp_path)
    _ = _inspect_and_approve(policy, TASK, path, diff)
    audit_report = json.loads(_gate_report(repo, "audit-core", base, path))
    audit_report["mode"] = "audit"
    raw = (json.dumps(audit_report, indent=2) + "\n").encode("utf-8")
    report_path = repo / ".aconsole-dev" / "gates" / "audit-core.json"

    def fake_execute(
        _argv: tuple[str, ...],
        *,
        cwd: str,
        env: dict[str, str],
        deadline: float,
        output_limit: int,
        written: list[int],
        write: Callable[[bytes], None],
    ) -> int:
        del cwd, env, deadline, output_limit, written
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(raw)
        write(b"audit failed\n")
        return 1

    monkeypatch.setattr(server, "execute_argv", fake_execute)
    plan = actions.build_plan("gate", ["--audit", "core"], Path(policy.repo), policy.repo, 30)
    assert not plan.gate_id

    code, _output = _run_plan(policy, plan)

    assert code == 1
    assert report_path.read_bytes() == raw
    assert not report_path.with_name("audit-core.raw.json").exists()


def _http_server(
    policy: server.Policy,
    holder: dict[str, str | None],
) -> tuple[server.ThreadingUnixHTTPServer, threading.Thread]:
    app = server.Application(policy, resolve_peer=lambda _pid: holder["name"])
    httpd = server.ThreadingUnixHTTPServer(policy.socket_path, app.handler_class())
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    for _ in range(50):
        if Path(policy.socket_path).exists():
            break
        time.sleep(0.02)
    return httpd, thread


def _post(sock_path: str, payload: dict[str, object]) -> tuple[int, bytes]:
    body = json.dumps(payload).encode()
    request = (
        "POST /run HTTP/1.0\r\n"
        "Host: localhost\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode() + body
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(sock_path)
    sock.sendall(request)
    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
    sock.close()
    raw = b"".join(chunks)
    head, _, payload_bytes = raw.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0]
    return int(status_line.split()[1]), payload_bytes


@pytest.fixture
def http_running(tmp_path: Path) -> Iterator[tuple[server.Policy, dict[str, str | None]]]:
    policy = _policy(tmp_path)
    holder: dict[str, str | None] = {"name": "agent-codex"}
    httpd, thread = _http_server(policy, holder)
    yield policy, holder
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


def test_non_main_container_is_denied_gate_review_over_http(http_running: tuple[server.Policy, dict[str, str | None]]) -> None:
    policy, holder = http_running
    holder["name"] = "agent-codex"

    status, body = _post(
        policy.socket_path,
        {"action": "gate-review", "args": ["inspect", TASK, PATH]},
    )

    assert status == 403
    assert b"action not permitted" in body
