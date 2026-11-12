"""Record lifecycle acceptance without reclassifying desktop/performance failures."""

# ruff: noqa: INP001, S101
from __future__ import annotations

import gzip
import hashlib
import json
import runpy
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "artifacts/mem2"
PERF = ROOT / "artifacts/perf7"
LABELS = ("mem2-sources-full", "mem2-full", "mem2-isolated", "mem2-base-isolated")
REGRESSION_COUNT = 3
PATHS = (
    "src/aconsole/ui/bootstrap.py", "src/aconsole/ui/views/window.py",
    "tests/gui/front/test_bootstrap_lifecycle.py", "tests/gui/front/test_window_badge_lifecycle.py",
    "tests/gui/front/test_git_scale_ui.py",
)


def main() -> None:
    for label in LABELS:
        for kind in ("sources", "lifecycle"):
            path = PERF / f"{label}-{kind}.jsonl"
            if path.exists():
                raw = path.read_bytes()
                packed = gzip.compress(raw, mtime=0)
                assert gzip.decompress(packed) == raw
                path.with_suffix(".jsonl.gz").write_bytes(packed)
                path.unlink()
    lifecycle = runpy.run_path(str(PERF / "analyze_lifecycle.py"))["analyze"]
    sources = runpy.run_path(str(PERF / "analyze_sources.py"))["analyze"]
    observed = sources(LABELS[0])
    assert not observed["late_by_callback"], observed["late_by_callback"]
    assert not observed["sources_at_end"], observed["sources_at_end"]
    assert not observed["tasks_at_end"], observed["tasks_at_end"]
    assert not observed["tasks_before_scale"], observed["tasks_before_scale"]
    benchmark = PATHS[-1]
    original = subprocess.check_output(["git", "show", f"8e1aa930:{benchmark}"], cwd=ROOT)  # noqa: S603,S607
    assert original == (ROOT / benchmark).read_bytes()
    red = ET.parse(OUTPUT / "native-red.xml").getroot()  # noqa: S314 -- local pytest output
    assert len(list(red.iter("testcase"))) == REGRESSION_COUNT
    assert sum(case.find("failure") is not None for case in red.iter("testcase")) == REGRESSION_COUNT
    known = set(json.loads((ROOT / "artifacts/qa20/validation.json").read_text())["runs"]["qa20-full"]["failures"])
    summaries = {label: lifecycle(label) for label in LABELS}
    summary = {key: value for key, value in observed.items()
               if key not in {"tasks_before_scale", "tasks_at_end", "sources_before_scale", "sources_at_end"}}
    summary.update(
        source_callbacks_before_scale=dict(Counter(source["callback"] for source in observed["sources_before_scale"])),
        task_count_before_scale=len(observed["tasks_before_scale"]),
        task_count_at_end=len(observed["tasks_at_end"]),
    )
    result = {
        "base": "8e1aa930", "benchmark_unchanged": True, "sources": summary, "runs": summaries,
        "additional_standard_full_failures": sorted(set(summaries["mem2-full"]["failures"]) - known),
        "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in PATHS},
    }
    (OUTPUT / "validation.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
