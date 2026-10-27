"""Phase gate placeholder scan and base commit on temporary repositories.

The private helpers are reached through a probe subprocess that prints JSON, so this module
never names a private symbol (basedpyright strict forbids that without a suppression).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from tools.gate import phase_gate as gate

ROOT = Path(__file__).resolve().parents[2]
_PROLOGUE = "import json\nfrom pathlib import Path\nfrom tools.gate import phase_gate as gate\n"


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=gate@example.com", "-c", "user.name=gate", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str) -> None:
    _ = _git(repo, "add", ".")
    _ = _git(repo, "commit", "-m", message)


def _fresh_repo(tmp: Path) -> None:
    _ = _git(tmp, "init")


def _base_repo(tmp: Path) -> str:
    _fresh_repo(tmp)
    _write(tmp / "src" / "aconsole" / "__init__.py", "")
    _write(tmp / "src" / "aconsole" / "mod.py", "def f() -> int:\n    return 1\n")
    _commit(tmp, "base")
    return _git(tmp, "rev-parse", "HEAD")


def _probe(body: str) -> str:
    """Run `body` in a fresh interpreter that imports phase_gate; return its stdout."""
    completed = subprocess.run(
        [sys.executable, "-c", _PROLOGUE + body],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _placeholder(tmp: Path, base: str) -> str:
    log_dir = tmp / "logs"
    log_dir.mkdir(exist_ok=True)
    return _probe(
        f"""
repo = Path({str(tmp)!r})
log_dir = Path({str(log_dir)!r})
step, found = gate._placeholder_step(repo, {base!r}, log_dir)
print(json.dumps({{'status': step['status'], 'rules': [item['rule'] for item in found]}}))
"""
    )


def _exit_message(call: str) -> str:
    """Stdout of a probe that prints the message of the SystemExit raised by `call`."""
    return _probe(
        f"""
try:
    {call}
except SystemExit as exc:
    print(json.dumps(str(exc.code)))
"""
    )


def test_uncommitted_placeholder_body_fails(tmp_path: Path) -> None:
    base = _base_repo(tmp_path)
    _write(tmp_path / "src" / "aconsole" / "mod.py", "def f() -> None: ...\n")
    assert json.loads(_placeholder(tmp_path, base)) == {"status": "fail", "rules": ["placeholder-body"]}


def test_untracked_placeholder_file_fails(tmp_path: Path) -> None:
    base = _base_repo(tmp_path)
    _write(tmp_path / "src" / "aconsole" / "new.py", "def g() -> None:\n    pass\n")
    assert json.loads(_placeholder(tmp_path, base)) == {"status": "fail", "rules": ["placeholder-body"]}


def test_clean_uncommitted_edit_passes_placeholder_step(tmp_path: Path) -> None:
    base = _base_repo(tmp_path)
    _write(tmp_path / "src" / "aconsole" / "mod.py", "def f() -> int:\n    return 2\n")
    assert json.loads(_placeholder(tmp_path, base)) == {"status": "pass", "rules": []}


def test_no_code_in_diff_is_explicit(tmp_path: Path) -> None:
    base = _base_repo(tmp_path)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    assert json.loads(_placeholder(tmp_path, base)) == {"status": "pass", "rules": []}
    assert (log_dir / "no_placeholders.log").read_text(encoding="utf-8") == "no code in diff\n"


def test_all_py_returns_scanned_files_and_skips_corpus_and_fixtures(tmp_path: Path) -> None:
    _fresh_repo(tmp_path)
    _write(tmp_path / "src" / "aconsole" / "a.py", "VALUE = 1\n")
    _write(tmp_path / "tests" / "core" / "test_a.py", "def test_a() -> None:\n    assert True is True or False\n")
    _write(tmp_path / "tools" / "t.py", "VALUE = 2\n")
    _write(tmp_path / "tests" / "gate" / "corpus" / "bad.py", "def f() -> None: ...\n")
    _write(tmp_path / "tests" / "gate" / "fixtures" / "f.py", "def f() -> None: ...\n")
    _write(tmp_path / "docs" / "x.py", "VALUE = 3\n")
    _commit(tmp_path, "scan")
    _write(tmp_path / "src" / "aconsole" / "new.py", "VALUE = 4\n")
    out = _probe(
        f"""
repo = Path({str(tmp_path)!r})
print(json.dumps(sorted(path.relative_to(repo).as_posix() for path in gate._all_py(repo))))
"""
    )
    assert json.loads(out) == [
        "src/aconsole/a.py",
        "src/aconsole/new.py",
        "tests/core/test_a.py",
        "tools/t.py",
    ]


def test_guard_script_returns_the_pinned_copy_from_base(tmp_path: Path) -> None:
    _fresh_repo(tmp_path)
    _write(tmp_path / "tools" / "gate" / "diff_guard.py", "BASE = 1\n")
    _commit(tmp_path, "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    _write(tmp_path / "tools" / "gate" / "diff_guard.py", "EDITED = 1\n")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    out = _probe(
        f"""
pinned = gate._guard_script(Path({str(tmp_path)!r}), {base!r}, Path({str(log_dir)!r}))
print(json.dumps([str(pinned.parent), pinned.read_text(encoding='utf-8')]))
"""
    )
    assert json.loads(out) == [str(log_dir), "BASE = 1\n"]
    assert (tmp_path / "tools" / "gate" / "diff_guard.py").read_text(encoding="utf-8") == "EDITED = 1\n"


def test_guard_script_falls_back_when_base_lacks_the_file(tmp_path: Path) -> None:
    base = _base_repo(tmp_path)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    out = _probe(
        f"print(json.dumps(str(gate._guard_script(Path({str(tmp_path)!r}), {base!r}, Path({str(log_dir)!r})))))\n"
    )
    assert json.loads(out) == str(gate.HERE / "diff_guard.py")


def test_base_is_parent_of_first_progress_commit(tmp_path: Path) -> None:
    base = _base_repo(tmp_path)
    progress = tmp_path / "docs" / "plan" / "core" / "progress" / "A9.1.md"
    _write(progress, "Base commit: stale\n")
    _commit(tmp_path, "progress")
    _write(progress, "Base commit: edited\n")
    _commit(tmp_path, "edit progress")
    out = _probe(f"print(json.dumps(gate._base_commit(Path({str(tmp_path)!r}), 'A9.1')))\n")
    assert json.loads(out) == base


def test_missing_progress_commit_records_head_as_base(tmp_path: Path) -> None:
    head = _base_repo(tmp_path)
    out = _probe(f"print(json.dumps(gate._base_commit(Path({str(tmp_path)!r}), 'A9.1')))\n")
    assert json.loads(out) == head
    bases = json.loads((tmp_path / "docs" / "plan" / "core" / "bases.json").read_text(encoding="utf-8"))
    assert bases["A9.1"] == head
    _write(tmp_path / "src" / "later.py", "X = 1\n")
    _commit(tmp_path, "later")
    again = _probe(f"print(json.dumps(gate._base_commit(Path({str(tmp_path)!r}), 'A9.1')))\n")
    assert json.loads(again) == head


def test_bases_key_overrides_history(tmp_path: Path) -> None:
    base = _base_repo(tmp_path)
    _write(tmp_path / "docs" / "plan" / "core" / "progress" / "A9.1.md", "Status: doing\n")
    _commit(tmp_path, "progress")
    later = _git(tmp_path, "rev-parse", "HEAD")
    _write(
        tmp_path / "docs" / "plan" / "core" / "bases.json",
        f'{{"A9.1": "{base}"}}\n',
    )
    out = _probe(f"print(json.dumps(gate._base_commit(Path({str(tmp_path)!r}), 'A9.1')))\n")
    assert json.loads(out) == base
    assert json.loads(out) != later


def test_bases_without_the_key_uses_history(tmp_path: Path) -> None:
    base = _base_repo(tmp_path)
    _write(tmp_path / "docs" / "plan" / "core" / "progress" / "A9.1.md", "Status: doing\n")
    _commit(tmp_path, "progress")
    _write(tmp_path / "docs" / "plan" / "core" / "bases.json", '{"A0.0r": "' + base + '"}\n')
    out = _probe(f"print(json.dumps(gate._base_commit(Path({str(tmp_path)!r}), 'A9.1')))\n")
    assert json.loads(out) == base


def test_bases_rejects_a_bad_sha(tmp_path: Path) -> None:
    _ = _base_repo(tmp_path)
    _write(tmp_path / "docs" / "plan" / "core" / "progress" / "A9.1.md", "Status: doing\n")
    _commit(tmp_path, "progress")
    bases = tmp_path / "docs" / "plan" / "core" / "bases.json"
    _write(bases, '{"A9.1": "not-a-sha"}\n')
    bad_text = _exit_message(f"gate._base_commit(Path({str(tmp_path)!r}), 'A9.1')")
    assert "not a 40-character sha" in bad_text
    _write(bases, '{"A9.1": "' + ("0" * 40) + '"}\n')
    missing = _exit_message(f"gate._base_commit(Path({str(tmp_path)!r}), 'A9.1')")
    assert "not a commit" in missing


def test_pytest_steps_are_split() -> None:
    core = gate.core_pytest_argv(Path("/repo"), Path("core.xml"), Path("cov.json"))
    ui = gate.ui_pytest_argv(Path("/repo"), Path("ui.xml"))
    assert core[0].endswith(".venv-core/bin/python")
    assert "tests/ui" not in core
    assert "tests/hostrun" in core
    assert ui[0].endswith(".venv-ui/bin/python")
    assert ui[-2:] == ["tests/ui", "tests/gui"]
    cov = [item for item in core if item.startswith("--cov=")]
    assert cov == [
        "--cov=aconsole.core",
        "--cov=aconsole.contracts",
        "--cov=aconsole.devcli",
        "--cov=aconsole.sidecar",
    ]
    ruff = gate.core_ruff_argv()
    assert "src/aconsole/core" in ruff
    assert "tools" in ruff
    assert "src/aconsole/ui" not in ruff
    assert "tests/ui" not in ruff
    assert "tests/gui" not in ruff


@pytest.mark.parametrize(
    ("path", "layer"),
    [
        ("src/aconsole/core/domain/agent.py", "domain"),
        ("src/aconsole/core/ports/paths.py", "ports"),
        ("src/aconsole/core/server.py", None),
    ],
)
def test_layer_of_maps_core_modules(path: str, layer: str | None) -> None:
    out = _probe(f"print(json.dumps(gate._layer_of({path!r})))\n")
    assert json.loads(out) == layer


def test_audit_core_writes_a_direct_report_without_a_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fresh_repo(tmp_path)
    _write(tmp_path / "src" / "aconsole" / "__init__.py", "")
    _commit(tmp_path, "base")
    monkeypatch.delenv("HOSTRUN_CONTAINER", raising=False)
    monkeypatch.setattr(gate, "REPO", tmp_path)
    ran: list[str] = []

    def fake_run(
        name: str,
        argv: list[str],
        log_dir: Path,
        *_rest: object,
        **_options: object,
    ) -> tuple[subprocess.CompletedProcess[str], gate.Step]:
        ran.append(name)
        log = log_dir / f"{name}.log"
        _ = log.write_text("", encoding="utf-8")
        step = gate.Step(name=name, argv=argv, exit_code=0, duration_s=0.0, status="pass", log=str(log))
        return subprocess.CompletedProcess(argv, 0, "", ""), step

    def fake_coverage(*_args: object) -> tuple[None, list[dict[str, object]]]:
        return None, []

    monkeypatch.setattr("tools.gate.phase_gate._run", fake_run)
    monkeypatch.setattr("tools.gate.phase_gate._layer_coverage", fake_coverage)
    assert gate.main(["--audit", "core"]) == 0
    _ = capsys.readouterr()
    assert "diff_guard" not in ran
    assert "pytest-core" in ran
    assert "pytest-ui" in ran
    assert not (tmp_path / ".aconsole-dev" / "current-task").exists()
    report = (tmp_path / ".aconsole-dev" / "gates" / "audit-core.json").read_text(encoding="utf-8")
    assert '"mode": "audit"' in report
    assert '"runner": "direct"' in report
    assert '"via_hostrun": false' in report
    assert '"verdict": "pass"' in report
    guard_log = tmp_path / ".aconsole-dev" / "gates" / "audit-core.logs" / "diff_guard.log"
    assert "audit mode" in guard_log.read_text(encoding="utf-8")


def test_audit_scope_other_than_core_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "REPO", Path("/nonexistent-repo"))
    with pytest.raises(SystemExit, match="takes one scope: core"):
        _ = gate.main(["--audit", "ui"])


def _task_repo(tmp: Path, progress: str) -> None:
    """A committed base with CORE.md and bases.json, then the progress file (committed)."""
    _fresh_repo(tmp)
    _write(
        tmp / "docs" / "plan" / "CORE.md",
        "**A9.1 · Пример** — **Файлы:** `x.py`. **Готово, когда:** `hostrun gate A9.1` → 0. **Запрещено:** baseline.\n",
    )
    _write(tmp / "src" / "aconsole" / "__init__.py", "")
    _commit(tmp, "base")
    base = _git(tmp, "rev-parse", "HEAD")
    _write(tmp / "docs" / "plan" / "core" / "bases.json", '{"A9.1": "' + base + '"}\n')
    _write(tmp / "docs" / "plan" / "core" / "progress" / "A9.1.md", progress)
    _commit(tmp, "progress")


def _fake_steps(
    name: str,
    argv: list[str],
    log_dir: Path,
    *_rest: object,
    **_options: object,
) -> tuple[subprocess.CompletedProcess[str], gate.Step]:
    """Every step passes with an empty report, except done_when, which runs the real checker."""
    log = log_dir / f"{name}.log"
    if name == "done_when":
        completed = subprocess.run(argv, capture_output=True, text=True, check=False)
    else:
        completed = subprocess.CompletedProcess(argv, 0, '{"violations": []}\n', "")
    _ = log.write_text(completed.stdout, encoding="utf-8")
    step = gate.Step(
        name=name,
        argv=argv,
        exit_code=completed.returncode,
        duration_s=0.0,
        status="pass" if completed.returncode == 0 else "fail",
        log=str(log),
    )
    return completed, step


def test_task_mode_reports_a_done_when_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _task_repo(tmp_path, "Status: doing\n\n## Done when\n- [ ] `hostrun gate A9.1` returns zero\n")
    monkeypatch.delenv("HOSTRUN_CONTAINER", raising=False)
    monkeypatch.setattr(gate, "REPO", tmp_path)
    monkeypatch.setattr("tools.gate.phase_gate._run", _fake_steps)
    monkeypatch.setattr("tools.gate.phase_gate._layer_coverage", lambda *_args: (None, []))
    assert gate.main(["A9.1"]) == 1
    _ = capsys.readouterr()
    report = json.loads((tmp_path / ".aconsole-dev" / "gates" / "A9.1.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "fail"
    assert "done_when" in [step["name"] for step in report["steps"]]
    found = [item for item in report["violations"] if item["gate"] == "done_when"]
    assert [item["rule"] for item in found] == ["done-when-rewritten"]
    assert found[0]["path"] == "docs/plan/core/progress/A9.1.md"


def test_audit_mode_does_not_run_done_when(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fresh_repo(tmp_path)
    _write(tmp_path / "src" / "aconsole" / "__init__.py", "")
    _commit(tmp_path, "base")
    monkeypatch.delenv("HOSTRUN_CONTAINER", raising=False)
    monkeypatch.setattr(gate, "REPO", tmp_path)
    ran: list[str] = []

    def record(
        name: str,
        argv: list[str],
        log_dir: Path,
        *rest: object,
        **options: object,
    ) -> tuple[subprocess.CompletedProcess[str], gate.Step]:
        ran.append(name)
        return _fake_steps(name, argv, log_dir, *rest, **options)

    monkeypatch.setattr("tools.gate.phase_gate._run", record)
    monkeypatch.setattr("tools.gate.phase_gate._layer_coverage", lambda *_args: (None, []))
    assert gate.main(["--audit", "core"]) == 0
    _ = capsys.readouterr()
    assert "done_when" not in ran
    assert "diff_guard" not in ran
