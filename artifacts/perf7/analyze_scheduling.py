"""Summarize existing observer intervals without rerunning or changing acceptance."""

from __future__ import annotations

import hashlib
import itertools
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HISTORY_ROWS = 10_000


def summarize(label: str) -> dict[str, object]:
    source = ROOT / f"{label}.jsonl"
    data = json.loads(source.read_text())
    # The observer has its own tick callback. This selects intervals ending after
    # scrolling begins; it is not the benchmark callback's exact sample window.
    frames = [frame for frame in data["frames"] if frame["rows"] >= HISTORY_ROWS and frame["scroll"] > 0]
    start = frames[0]["at"] - frames[0]["wall"]
    end = frames[-1]["at"]
    pauses = []
    collections: dict[int, float] = {}
    for event in data["gc"]:
        generation = event["generation"]
        if event["phase"] == "start":
            collections[generation] = event["at"]
        elif generation in collections:
            began = collections.pop(generation)
            overlap = max(0.0, min(end, event["at"]) - max(start, began))
            if overlap:
                pauses.append(overlap * 1000)
    phase_intervals: dict[str, list[float]] = {}
    for first, second in itertools.pairwise(data["phases"]):
        if first["scroll"] > 0 and first["rows"] >= HISTORY_ROWS:
            key = first["name"] + " -> " + second["name"]
            phase_intervals.setdefault(key, []).append((second["wall"] - first["wall"]) / 1e6)
    return {
        "input": source.name,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "acceptance_report": (ROOT / f"{label}-scale.txt").read_text().strip(),
        "observer_frames": len(frames),
        "observer_window_seconds": [start, end],
        "observer_median_ms": {
            key: statistics.median(frame[key] for frame in frames) * 1000
            for key in ("wall", "thread", "cpu")
        },
        "observer_sum_seconds": {
            key: sum(frame[key] for frame in frames) for key in ("wall", "thread", "cpu")
        },
        "gc_overlapping_pauses_ms": pauses,
        "phase_median_ms": {key: statistics.median(values) for key, values in phase_intervals.items()},
        "phase_samples": {key: len(values) for key, values in phase_intervals.items()},
        "cpu_stat_before": data["cpu_stat_before"],
        "cpu_stat_after": data["cpu_stat_after"],
        "cpu_max": data["cpu_max"],
        "load_before": data["load_before"],
        "load_after": data["load_after"],
        "renderer": data["renderer"],
    }


if __name__ == "__main__":
    labels = sys.argv[1:] or ["grid-styled", "grid-history", "grid-full"]
    sys.stdout.write(json.dumps({label: summarize(label) for label in labels}, indent=2) + "\n")
