"""Run the A0.2 checks and write .aconsole-dev/gates/<TASK>.json."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict


class Step(TypedDict):
    name: str
    argv: list[str]
    exit_code: int
    duration_s: float
    status: str
    log: str


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SUMMARY_MAX = 40
PROGRESS_DIR = "docs/plan/core/progress"
BASES_FILE = "docs/plan/core/bases.json"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
SCAN_ROOTS = ("src/", "tests/", "tools/", "spikes/")
# Corpus and fixtures hold deliberate violations for the checkers.
SCAN_SKIP = ("tests/gate/corpus/", "tests/gate/fixtures/")
# Explicit path -> layer table (CORE.md section 2, item 4). First match wins. A None floor means
# the layer has no per-layer floor and only the global fail_under applies. Keys ending in "/"
# match a directory prefix; other keys match one file exactly.
LAYER_MAP: tuple[tuple[str, str, float | None], ...] = (
    ("src/aconsole/core/domain/", "domain", 90.0),
    ("src/aconsole/core/ports/", "ports", None),
    ("src/aconsole/core/application/", "application", 90.0),
    ("src/aconsole/core/infrastructure/", "infrastructure", 70.0),
    ("src/aconsole/core/__init__.py", "core-entry", None),
    ("src/aconsole/core/__main__.py", "core-entry", None),
    ("src/aconsole/core/bootstrap.py", "core-entry", None),
    ("src/aconsole/contracts/", "contracts", None),
    ("src/aconsole/devcli/", "devcli", None),
    ("src/aconsole/sidecar/", "sidecar", None),
    ("src/aconsole/ui/", "ui", None),
    ("src/aconsole/__init__.py", "package", None),
    ("src/aconsole/__main__.py", "package", None),
)
LAYER_FLOORS: dict[str, float | None] = {layer: floor for _key, layer, floor in LAYER_MAP}


def _run(
    name: str,
    argv: list[str],
    log_dir: Path,
    cwd: Path = REPO,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Step]:
    started = time.monotonic()
    completed = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False, env=env)
    duration = round(time.monotonic() - started, 3)
    log = log_dir / f"{name}.log"
    log.write_text(completed.stdout + "\n--- stderr ---\n" + completed.stderr, encoding="utf-8")
    step = Step(
        name=name,
        argv=argv,
        exit_code=completed.returncode,
        duration_s=duration,
        status="pass" if completed.returncode == 0 else "fail",
        log=str(log),
    )
    return completed, step


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    if check and completed.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed


AUDITED = "audit"


def _base_from_file(repo: Path, phase: str) -> str | None:
    """Return the sha recorded for ``phase``, ``"audit"``, or None when the file has no such key.

    ``"audit"`` marks a task accepted before ROUND6 (K1.5): its old base was blind, so the gate checks the whole
    core scope, as ``--audit core`` does, instead of a diff.
    """
    path = repo / BASES_FILE
    if not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{BASES_FILE} is not JSON") from exc
    if not isinstance(parsed, dict) or phase not in parsed:
        return None
    value = parsed[phase]
    if value == AUDITED:
        return AUDITED
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise SystemExit(f"{BASES_FILE} key {phase} is not a 40-character sha")
    probed = _git(repo, "cat-file", "-e", f"{value}^{{commit}}", check=False)
    if probed.returncode != 0:
        raise SystemExit(f"{BASES_FILE} key {phase} is not a commit")
    return value


def _record_base(repo: Path, phase: str) -> str:
    """Write HEAD as the task base into ``bases.json`` on the first gate run of a task.

    The plan is not tracked by git since the history rewrite, so the progress-file history no longer gives a base.
    The task commit comes after the gate passes, so HEAD at the first run is the state before the task.
    """
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    path = repo / BASES_FILE
    bases: dict[str, object] = {}
    if path.is_file():
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            bases = {str(key): value for key, value in parsed.items()}
    bases[phase] = head
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bases, indent=2) + "\n", encoding="utf-8")
    return head


def _base_commit(repo: Path, phase: str) -> str:
    """Prefer ``bases.json`` for this task, then the progress-file history, else record HEAD."""
    recorded = _base_from_file(repo, phase)
    if recorded is not None:
        return recorded
    progress = f"{PROGRESS_DIR}/{phase}.md"
    added = _git(repo, "log", "--diff-filter=A", "--format=%H", "--", progress).stdout.split()
    if not added:
        return _record_base(repo, phase)
    first = added[-1]
    parent = _git(repo, "rev-parse", "--verify", "--quiet", f"{first}^", check=False)
    if parent.returncode != 0:
        raise SystemExit(f"{progress} was added in root commit {first}; there is no base commit")
    return parent.stdout.strip()


def core_pytest_argv(repo: Path, junit: Path, cov_json: Path) -> list[str]:
    python = repo / ".venv-core" / "bin" / "python"
    return [
        str(python),
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        f"--junitxml={junit}",
        "--cov=aconsole.core",
        "--cov=aconsole.contracts",
        "--cov=aconsole.devcli",
        "--cov=aconsole.sidecar",
        f"--cov-report=json:{cov_json}",
        "--cov-fail-under=90",
        "tests/core",
        "tests/contracts",
        "tests/gate",
        "tests/hostrun",
    ]


# P8: ruff stays on the core packages and the shared tools. UI is the other pytest step.
_CORE_RUFF = (
    "src/aconsole/core",
    "src/aconsole/contracts",
    "src/aconsole/devcli",
    "src/aconsole/sidecar",
    "tools",
    "tests/core",
    "tests/contracts",
    "tests/gate",
)


def core_ruff_argv() -> list[str]:
    return [_tool("ruff"), "check", "--output-format", "json", *_CORE_RUFF]


def ui_pytest_argv(repo: Path, junit: Path) -> list[str]:
    python = repo / ".venv-ui" / "bin" / "python"
    return [
        str(python),
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        f"--junitxml={junit}",
        # Front GUI tests belong to the front gate (Luna's verdict); the core gate keeps the client GUI tests.
        "--ignore=tests/gui/front",
        "tests/ui",
        "tests/gui",
    ]


def _tool(name: str) -> str:
    return str(Path(sys.executable).parent / name)


def _gate_violation(gate: str, rule: str, path: str, detail: str, line: object = None) -> dict[str, object]:
    return {"gate": gate, "rule": rule, "path": path, "line": line, "detail": detail}


def _changed_py(repo: Path, base: str) -> list[Path]:
    """Tracked edits against base (committed or not) plus untracked files, limited to scanned .py files."""
    tracked = _git(repo, "diff", "--name-only", base).stdout.splitlines()
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    found: set[str] = set()
    for name in (*tracked, *untracked):
        if (
            name.endswith(".py")
            and name.startswith(SCAN_ROOTS)
            and not name.startswith(SCAN_SKIP)
            and (repo / name).is_file()
        ):
            found.add(name)
    return [repo / name for name in sorted(found)]


def _all_py(repo: Path) -> list[Path]:
    """Every tracked and untracked scanned .py file: the audit scope."""
    tracked = _git(repo, "ls-files").stdout.splitlines()
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    found = {
        name
        for name in (*tracked, *untracked)
        if name.endswith(".py")
        and name.startswith(SCAN_ROOTS)
        and not name.startswith(SCAN_SKIP)
        and (repo / name).is_file()
    }
    return [repo / name for name in sorted(found)]


def _guard_script(repo: Path, base: str, log_dir: Path) -> Path:
    """The diff guard as it was at the task base, so an edit in the task cannot weaken it."""
    shown = _git(repo, "show", f"{base}:tools/gate/diff_guard.py", check=False)
    if shown.returncode != 0:
        return HERE / "diff_guard.py"
    pinned = log_dir / "diff_guard.base.py"
    pinned.write_text(shown.stdout, encoding="utf-8")
    return pinned


def _done_when_step(repo: Path, base: str, phase: str, log_dir: Path) -> tuple[Step, list[dict[str, object]]]:
    """The progress file keeps the brief's "Готово, когда" word for word, and `done` needs every item ticked."""
    completed, step = _run(
        "done_when",
        [sys.executable, str(HERE / "done_when.py"), "--repo", str(repo), "--base", base, "--phase", phase],
        log_dir,
    )
    try:
        parsed = json.loads(completed.stdout)
        hits = parsed.get("violations", []) if isinstance(parsed, dict) else []
    except json.JSONDecodeError:
        hits = [{"rule": "done-when-output-unreadable", "path": "", "detail": completed.stdout[-300:]}]
    found: list[dict[str, object]] = [
        {
            "gate": "done_when",
            "rule": str(item.get("rule", "")),
            "path": str(item.get("path", "")),
            "line": None,
            "detail": str(item.get("detail", "")),
        }
        for item in hits
        if isinstance(item, dict)
    ]
    return step, found


def _placeholder_step(repo: Path, base: str | None, log_dir: Path) -> tuple[Step, list[dict[str, object]]]:
    files = _all_py(repo) if base is None else _changed_py(repo, base)
    if not files and any((repo / "src" / "aconsole").rglob("*.py")):
        message = "no code in diff"
        log = log_dir / "no_placeholders.log"
        log.write_text(message + "\n", encoding="utf-8")
        step = Step(name="no_placeholders", argv=[], exit_code=0, duration_s=0.0, status="pass", log=str(log))
        return step, []
    argv = [sys.executable, str(HERE / "no_placeholders.py"), "--json", *(str(path) for path in files)]
    completed, step = _run("no_placeholders", argv, log_dir, repo)
    try:
        parsed = json.loads(completed.stdout)
        found = parsed.get("violations", []) if isinstance(parsed, dict) else []
    except json.JSONDecodeError:
        found = []
    violations: list[dict[str, object]] = []
    if isinstance(found, list):
        for item in found:
            if isinstance(item, dict):
                violations.append(
                    {
                        "gate": "no_placeholders",
                        "rule": str(item.get("rule", "")),
                        "path": str(item.get("file", "")),
                        "line": item.get("line"),
                        "detail": str(item.get("message", "")),
                    }
                )
    return step, violations


def _layer_of(path: str) -> str | None:
    for key, layer, _floor in LAYER_MAP:
        if path == key or (key.endswith("/") and path.startswith(key)):
            return layer
    return None


def _layer_coverage(repo: Path, cov_json: Path) -> tuple[dict[str, object] | None, list[dict[str, object]]]:
    """Per-layer coverage from the pytest JSON report; every src/aconsole .py file must map to a layer."""
    problems: list[dict[str, object]] = []
    for source in sorted((repo / "src" / "aconsole").rglob("*.py")):
        rel = source.relative_to(repo).as_posix()
        if _layer_of(rel) is None:
            problems.append(_gate_violation("coverage_layers", "unmapped-path", rel, "no layer in LAYER_MAP"))
    try:
        data = json.loads(cov_json.read_text(encoding="utf-8"))
        covered: dict[str, int] = {}
        statements: dict[str, int] = {}
        for rel, entry in data["files"].items():
            layer = _layer_of(rel)
            if layer is None:
                continue
            summary = entry["summary"]
            covered[layer] = covered.get(layer, 0) + summary["covered_lines"] + summary["covered_branches"]
            statements[layer] = statements.get(layer, 0) + summary["num_statements"] + summary["num_branches"]
        percent_total = float(data["totals"]["percent_covered"])
    except OSError, json.JSONDecodeError, KeyError, TypeError:
        problems.append(_gate_violation("coverage_layers", "report-missing", str(cov_json), "coverage JSON unreadable"))
        return None, problems
    layers: dict[str, dict[str, object]] = {}
    for layer, floor in LAYER_FLOORS.items():
        total = statements.get(layer, 0)
        percent = round(100.0 * covered.get(layer, 0) / total, 2) if total else None
        layers[layer] = {"percent": percent, "floor": floor}
        if floor is None:
            continue
        if percent is None:
            problems.append(_gate_violation("coverage_layers", "no-statements", layer, "no statements measured"))
        elif percent < floor:
            problems.append(_gate_violation("coverage_layers", "below-floor", layer, f"{percent}% < {floor}%"))
    coverage: dict[str, object] = {"percent": round(percent_total, 2), "fail_under": 90, "layers": layers}
    return coverage, problems


def main(argv: list[str]) -> int:
    audit = len(argv) == 2 and argv[0] == "--audit"
    if audit and argv[1] != "core":
        raise SystemExit("--audit takes one scope: core")
    phase = f"audit-{argv[1]}" if audit else (argv[0] if argv else "A0.2")
    base = None if audit else _base_commit(REPO, phase)
    if base == AUDITED:
        audit = True
        base = None
    out = REPO / ".aconsole-dev" / "gates" / f"{phase}.json"
    log_dir = out.parent / f"{phase}.logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if base is not None:
        (REPO / ".aconsole-dev" / "current-task").write_text(f"{phase}\n{base}\n", encoding="utf-8")
    runner = os.environ.get("HOSTRUN_CONTAINER", "")
    started = datetime.now(UTC).isoformat(timespec="seconds")
    head = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "-C", str(REPO), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    )
    steps: list[Step] = []
    violations: list[dict[str, object]] = []
    counts: dict[str, int] = {}
    step, placeholder_found = _placeholder_step(REPO, base, log_dir)
    counts["placeholder_bypass"] = len(placeholder_found)
    violations.extend(placeholder_found)
    steps.append(step)
    if base is None:
        guard_log = log_dir / "diff_guard.log"
        guard_log.write_text("audit mode: no task base, the diff guard does not apply\n", encoding="utf-8")
        step = Step(name="diff_guard", argv=[], exit_code=0, duration_s=0.0, status="pass", log=str(guard_log))
        guard: object = []
    else:
        guard_script = _guard_script(REPO, base, log_dir)
        completed, step = _run(
            "diff_guard",
            [sys.executable, str(guard_script), "--repo", str(REPO), "--base", base, "--json"],
            log_dir,
        )
        try:
            parsed = json.loads(completed.stdout)
            guard = parsed.get("violations", []) if isinstance(parsed, dict) else []
        except json.JSONDecodeError:
            guard = [{"rule": "guard-output-unreadable", "path": "", "detail": completed.stdout[-300:]}]
    counts["guard_violations"] = len(guard) if isinstance(guard, list) else 0
    if isinstance(guard, list):
        for item in guard:
            if isinstance(item, dict):
                violations.append(
                    {
                        "gate": "diff_guard",
                        "rule": str(item.get("rule", "")),
                        "path": str(item.get("path", "")),
                        "line": None,
                        "detail": str(item.get("detail", "")),
                    }
                )
    steps.append(step)
    if base is not None:
        step, done_when_found = _done_when_step(REPO, base, phase, log_dir)
        counts["done_when"] = len(done_when_found)
        violations.extend(done_when_found)
        steps.append(step)
    for name, argv in (
        ("ruff", core_ruff_argv()),
        ("basedpyright", [_tool("basedpyright"), "--outputjson"]),
        ("check_no_raw_sql", [sys.executable, str(REPO / "tools" / "check_no_raw_sql.py"), "src", "tests"]),
        ("check_no_js", [sys.executable, str(REPO / "tools" / "check_no_js.py"), "."]),
        ("check_contracts", [sys.executable, str(REPO / "tools" / "check_contracts.py"), str(REPO)]),
        (
            "import_linter",
            [_tool("lint-imports"), "--config", str(REPO / "pyproject.toml")],
        ),
    ):
        completed, step = _run(name, argv, log_dir)
        counts[name] = 0 if completed.returncode == 0 else 1
        if completed.returncode != 0:
            violations.append(
                {
                    "gate": name,
                    "rule": "failed",
                    "path": "",
                    "line": None,
                    "detail": (completed.stdout + completed.stderr)[-300:],
                }
            )
        steps.append(step)
    junit = log_dir / "junit-core.xml"
    cov_json = log_dir / "coverage.json"
    completed, step = _run("pytest-core", core_pytest_argv(REPO, junit, cov_json), log_dir)
    steps.append(step)
    ui_env = os.environ.copy()
    ui_env.pop("PYTHON_GIL", None)
    completed, step = _run(
        "pytest-ui",
        ui_pytest_argv(REPO, log_dir / "junit-ui.xml"),
        log_dir,
        env=ui_env,
    )
    steps.append(step)
    coverage, layer_problems = _layer_coverage(REPO, cov_json)
    counts["coverage_layers"] = len(layer_problems)
    violations.extend(layer_problems)
    failed = [item["name"] for item in steps if item["status"] != "pass"]
    verdict = "pass" if not failed and not violations else "fail"
    report = {
        "schema_version": 1,
        "phase_id": phase,
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "mode": "audit" if audit else "task",
        "runner": runner or "direct",
        "via_hostrun": bool(runner),
        "repo": {"path": str(REPO), "base": base, "head": head, "dirty_worktree": dirty},
        "steps": steps,
        "counts": counts,
        "coverage": coverage,
        "violations_total": len(violations),
        "violations": violations[:300],
        "verdict": verdict,
        "failed_steps": failed,
    }
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [f"Phase {phase} gate: {verdict.upper()} ({len(failed)} failing step(s): {', '.join(failed) or 'none'})"]
    if not runner:
        lines.append("runner: direct (not through hostrun) - this report does not count as a task pass")
    for item in steps:
        lines.append(f"- {item['name']}: exit={item['exit_code']} {item['duration_s']}s")
    if coverage is not None:
        lines.append(f"coverage: {coverage['percent']}% (min 90)")
    lines.append(f"report: {out}")
    sys.stdout.write("\n".join(lines[:SUMMARY_MAX]) + "\n")
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
