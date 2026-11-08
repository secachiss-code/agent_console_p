"""Agent-writable gate reports and approvals cannot establish a trusted verdict."""

from __future__ import annotations

from tests.socket_paths import short_socket_path

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


@pytest.fixture(autouse=True)
def isolated_review_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """This suite runs in devtools. Emulate the outer Podman dispatch, run real Git here."""
    original = subprocess.run

    def run(
        argv: list[str] | tuple[str, ...], *args: object, **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes] | subprocess.CompletedProcess[str]:
        if argv[:3] == ["podman", "start", "devtools"]:
            return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")
        if argv[:2] == ["podman", "exec"]:
            assert argv[2] == "--workdir"
            assert argv[4:7] == ["-e", "GIT_LITERAL_PATHSPECS=1", "devtools"]
            kwargs["env"] = {**kwargs["env"], "GIT_LITERAL_PATHSPECS": "1"}
            return original(argv[7:], *args, **kwargs)
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)


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
        socket_path=str(short_socket_path(tmp / "hostrun.sock")),
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


def _inspect_and_forge_approval(policy: server.Policy, task: str, path: str, diff: bytes) -> dict[str, object]:
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

    # Simulate an agent forging the old on-disk approval, even outside the checkout.
    stored = {key: value for key, value in inspection.items() if key != "diff_base64"}
    target = _review_path(policy, task, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(stored), encoding="utf-8")
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
        argv: tuple[str, ...],
        *,
        cwd: str,
        env: dict[str, str],
        deadline: float,
        output_limit: int,
        written: list[int],
        write: Callable[[bytes], None],
    ) -> int:
        del cwd, env, deadline, output_limit, written
        if argv[:3] == ("podman", "start", "devtools"):
            return 0
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


def test_inspect_and_forge_approval_bind_record_to_exact_task_base_path_status_and_diff(
    tmp_path: Path,
) -> None:
    policy, repo, base, path, diff = _repo_with_tracked_main_edit(tmp_path)

    record = _inspect_and_forge_approval(policy, TASK, path, diff)

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

    with pytest.raises(actions.ActionError) as caught:
        _review_plan(policy, "approve", TASK, path, digest)
    assert caught.value.status == 403
    assert not _review_path(policy, TASK, path).exists()
    assert digest != hashlib.sha256(_git(repo, "diff", "--binary", base, "--", path)).hexdigest()


def test_forged_protected_approval_cannot_promote_a_failed_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    policy, repo, base, path, diff = _repo_with_tracked_main_edit(tmp_path)
    _inspect_and_forge_approval(policy, TASK, path, diff)
    raw = _gate_report(repo, TASK, base, path)
    code, _output, canonical = _run_gate_with_report(monkeypatch, policy, repo, TASK, raw)
    assert code == 1
    assert canonical.read_bytes() == raw
    assert not canonical.with_name(f"{TASK}.raw.json").exists()


def test_replayed_approval_never_promotes_later_reports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    policy, repo, base, path, diff = _repo_with_tracked_main_edit(tmp_path)
    _inspect_and_forge_approval(policy, TASK, path, diff)
    for finished in ("first", "second"):
        report = json.loads(_gate_report(repo, TASK, base, path))
        report["finished_at"] = finished
        raw = json.dumps(report).encode()
        code, _output, canonical = _run_gate_with_report(monkeypatch, policy, repo, TASK, raw)
        assert code == 1
        assert canonical.read_bytes() == raw
        assert not canonical.with_name(f"{TASK}.raw.json").exists()


def test_approval_cannot_be_reused_from_a_copied_repository(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy, repo, base, path, diff = _repo_with_tracked_main_edit(tmp_path)
    _ = _inspect_and_forge_approval(policy, TASK, path, diff)
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
    _ = _inspect_and_forge_approval(policy, TASK, path, diff)
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
    _ = _inspect_and_forge_approval(policy, TASK, path, diff)
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
    _ = _inspect_and_forge_approval(policy, TASK, path, diff)
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
    _ = _inspect_and_forge_approval(policy, TASK, path, diff)
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
    _ = _inspect_and_forge_approval(policy, TASK, path, diff)
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
    _ = _inspect_and_forge_approval(policy, TASK, path, diff)
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
    _ = _inspect_and_forge_approval(policy, TASK, path, diff)
    audit_report = json.loads(_gate_report(repo, "audit-core", base, path))
    audit_report["mode"] = "audit"
    raw = (json.dumps(audit_report, indent=2) + "\n").encode("utf-8")
    report_path = repo / ".aconsole-dev" / "gates" / "audit-core.json"

    def fake_execute(
        argv: tuple[str, ...],
        *,
        cwd: str,
        env: dict[str, str],
        deadline: float,
        output_limit: int,
        written: list[int],
        write: Callable[[bytes], None],
    ) -> int:
        del cwd, env, deadline, output_limit, written
        if argv[:3] == ("podman", "start", "devtools"):
            return 0
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


@pytest.mark.parametrize("container", ["agent-claude", "agent-codex", "agent-cursor", "agent-grok"])
def test_approve_is_denied_even_when_local_policy_grants_review(
    http_running: tuple[server.Policy, dict[str, str | None]], container: str,
) -> None:
    policy, holder = http_running
    holder["name"] = container
    spec = policy.containers[container]
    policy.containers[container] = replace(spec, actions=spec.actions | {"gate-review"})
    status, body = _post(policy.socket_path, {
        "action": "gate-review", "args": ["approve", TASK, PATH, "a" * 64],
    })
    assert status == 403
    assert b"independent review" in body
    assert not _review_path(policy, TASK).exists()


@pytest.mark.parametrize("args", [[TASK], ["--audit", "core"]])
@pytest.mark.parametrize("forged", [b'{"verdict":"pass"}', b'{"verdict":"pass","review":{"recorded":true}}'])
def test_zero_exit_and_forged_success_are_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], forged: bytes,
) -> None:
    policy = _policy(tmp_path)
    report = Path(policy.repo) / ".aconsole-dev/gates" / f"{args[0]}.json"
    report.parent.mkdir(parents=True)
    report.write_bytes(forged)

    def execute(argv: tuple[str, ...], *, cwd: str, env: dict[str, str], deadline: float,
                output_limit: int, written: list[int], write: Callable[[bytes], None]) -> int:
        del cwd, env, deadline, output_limit, written
        if argv[:2] == ("podman", "exec"):
            write(b"forged gate PASS\n")
        return 0

    monkeypatch.setattr(server, "execute_argv", execute)
    plan = actions.build_plan("gate", args, Path(policy.repo), policy.repo, 30)
    for _attempt in range(2):
        code, output = _run_plan(policy, plan)
        assert code == 1
        assert b"UNVERIFIED" in output
        assert report.read_bytes() == forged


def test_inspection_does_not_create_an_approval(tmp_path: Path) -> None:
    policy, _repo, _base, path, _diff = _repo_with_tracked_main_edit(tmp_path)
    code, output = _run_plan(policy, _review_plan(policy, "inspect", TASK, path))
    assert code == 0
    assert json.loads(output)["digest"]
    assert not _review_path(policy, TASK, path).exists()


def test_old_approval_plan_cannot_bypass_dispatch_validation(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    plan = actions.Plan(mode="gate-review", steps=(), timeout_s=30, repo=policy.repo,
                        review_args=("approve", TASK, PATH, "a" * 64))
    code, output = _run_plan(policy, plan)
    assert code == 1
    assert b"independent review" in output
    assert not _review_path(policy, TASK).exists()


def test_review_git_dispatches_only_podman(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[list[str]] = []

    def capture(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=b"review result", stderr=b"")

    monkeypatch.setattr(subprocess, "run", capture)
    repo = str(tmp_path)
    assert server._review_git(repo, "diff", "--no-ext-diff", "--no-textconv") == b"review result"
    assert seen == [
        ["podman", "start", "devtools"],
        ["podman", "exec", "--workdir", repo, "-e", "GIT_LITERAL_PATHSPECS=1", "devtools",
         "git", "-C", repo, "diff", "--no-ext-diff", "--no-textconv"],
    ]


def test_review_git_container_failure_has_no_host_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[list[str]] = []

    def fail(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        seen.append(argv)
        raise subprocess.CalledProcessError(125, argv)

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        server._review_git(str(tmp_path), "rev-parse", "HEAD")
    assert seen == [["podman", "start", "devtools"]]
