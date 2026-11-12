"""Summarize scalar Python-source observations, including cross-test dispatch."""

# ruff: noqa: INP001
from __future__ import annotations

import gzip
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent


def analyze(label: str) -> dict[str, object]:
    path = OUTPUT / f"{label}-sources.jsonl"
    if path.exists():
        raw = path.read_text()
    else:
        path = path.with_suffix(".jsonl.gz")
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            raw = stream.read()
    events = [json.loads(line) for line in raw.splitlines()]
    snapshots = [event for event in events if event["stage"] in {"setup", "teardown"}]
    late = [event for event in events if event["stage"] == "late"]
    scale = next(event for event in snapshots if event["stage"] == "setup" and event["nodeid"].endswith(
        "::test_scrolling_a_long_history"
    ))
    during_scale = [event for event in late if event["current"] == scale["nodeid"]]
    return {
        "snapshots": len(snapshots),
        "late_dispatches": len(late),
        "late_by_callback": dict(Counter(event["source"]["callback"] for event in late)),
        "late_dispatches_during_scale": len(during_scale),
        "late_callbacks_during_scale": dict(Counter(event["source"]["callback"] for event in during_scale)),
        "sources_before_scale": scale["sources"],
        "tasks_before_scale": scale["tasks"],
        "sources_at_end": snapshots[-1]["sources"],
        "tasks_at_end": snapshots[-1]["tasks"],
        "maximum_sources": max(len(event["sources"]) for event in snapshots),
        "maximum_tasks": max(len(event["tasks"]) for event in snapshots),
        "evidence_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()},
    }


if __name__ == "__main__":
    sys.stdout.write(json.dumps({label: analyze(label) for label in sys.argv[1:]}, indent=2) + "\n")
