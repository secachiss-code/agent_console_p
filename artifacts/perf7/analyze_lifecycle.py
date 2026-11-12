"""Summarize reproducible GTK lifecycle observations; never rewrite acceptance."""

# ruff: noqa: INP001
from __future__ import annotations

import gzip
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "artifacts/perf7"


def analyze(label: str) -> dict[str, object]:
    path = OUTPUT / f"{label}-lifecycle.jsonl"
    if path.exists():
        raw = path.read_text()
    else:
        path = path.with_suffix(".jsonl.gz")
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            raw = stream.read()
    events = [json.loads(line) for line in raw.splitlines()]
    xml = ET.parse(OUTPUT / f"{label}.xml").getroot()  # noqa: S314 -- locally generated pytest output
    scale = next(event for event in events if event["stage"] == "setup" and event["nodeid"].endswith(
        "::test_scrolling_a_long_history"
    ))
    mapped = [window for window in scale["windows"] if window["mapped"]]
    phases = Counter()
    for event in events:
        phases[event["stage"]] += 1
    data = json.loads((OUTPUT / f"{label}.jsonl").read_text())
    return {
        "events_by_stage": dict(phases),
        "windows_before_scale": len(scale["windows"]),
        "mapped_before_scale": len(mapped),
        "mapped_applications_before_scale": dict(Counter(window["application"] for window in mapped)),
        "fds_before_scale": scale["fds"],
        "threads_before_scale": scale["threads"],
        "windows_at_end": len(events[-1]["windows"]),
        "maximum_windows": max(len(event["windows"]) for event in events),
        "scale": (OUTPUT / f"{label}-scale.txt").read_text().strip(),
        "passed": sum(case.find("failure") is None and case.find("error") is None
                      and case.find("skipped") is None for case in xml.iter("testcase")),
        "failures": [case.attrib["classname"] + "::" + case.attrib["name"]
                     for case in xml.iter("testcase") if case.find("failure") is not None],
        "errors": sum(case.find("error") is not None for case in xml.iter("testcase")),
        "skipped": sum(case.find("skipped") is not None for case in xml.iter("testcase")),
        "renderer": data["renderer"],
        "gtk_version": data["gtk_version"],
        "load_before": data["load_before"],
        "load_after": data["load_after"],
        "evidence_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (
            path, OUTPUT / f"{label}.jsonl", OUTPUT / f"{label}.xml", OUTPUT / f"{label}-scale.txt",
            OUTPUT / f"{label}-gl.txt",
        )},
    }


if __name__ == "__main__":
    sys.stdout.write(json.dumps({label: analyze(label) for label in sys.argv[1:]}, indent=2) + "\n")
