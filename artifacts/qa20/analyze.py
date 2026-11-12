"""Keep watchdog acceptance separate from desktop and performance failures."""

# ruff: noqa: INP001
from __future__ import annotations

import hashlib
import json
import runpy
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "artifacts/qa20"
PERF = ROOT / "artifacts/perf7"
BASE = "44fc9f2b"


def main() -> None:
    lifecycle = runpy.run_path(str(PERF / "analyze_lifecycle.py"))["analyze"]
    sources = runpy.run_path(str(PERF / "analyze_sources.py"))["analyze"]
    observed = sources("qa20-sources-full")
    late_tests = {callback: count for callback, count in observed["late_by_callback"].items()
                  if callback.startswith("tests.")}
    summaries = {label: lifecycle(label) for label in ("qa20-sources-full", "qa20-full", "qa20-isolated")}
    paths = [
        "tests/gui/watchdog.py", "tests/gui/front/test_watchdog_cleanup.py",
        "tests/gui/front/test_git_pane.py", "tests/gui/front/test_review_page.py",
        "tests/gui/front/test_approval_panel.py", "tests/gui/front/test_approval_alerts.py",
        "tests/gui/front/test_git_scale_ui.py",
    ]
    benchmark = paths[-1]
    original = subprocess.check_output(["git", "show", f"{BASE}:{benchmark}"], cwd=ROOT)  # noqa: S603,S607 -- fixed local Git object
    assert original == (ROOT / benchmark).read_bytes(), "benchmark changed"  # noqa: S101
    assert not late_tests, late_tests  # noqa: S101 -- acceptance for the targeted watchdog correction
    source_summary = {key: value for key, value in observed.items()
                      if key not in {"tasks_before_scale", "tasks_at_end", "sources_before_scale", "sources_at_end"}}
    source_summary.update(
        source_callbacks_before_scale=dict(Counter(source["callback"] for source in observed["sources_before_scale"])),
        task_count_before_scale=len(observed["tasks_before_scale"]),
        task_count_at_end=len(observed["tasks_at_end"]),
    )
    result = {
        "base": BASE, "late_test_callbacks": late_tests,
        "benchmark_unchanged": True, "sources": source_summary, "runs": summaries,
        "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths},
    }
    (OUTPUT / "validation.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
