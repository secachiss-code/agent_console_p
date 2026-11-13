# ruff: noqa: INP001, S101, S314, T201, PLR2004
# Standalone verifier: assertions and stdout are intentional; XML is local pytest output.
"""Verify retained QA32 evidence without rerunning tests."""

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/qa32"
data = json.loads((OUT / "results.json").read_text())
assert data["sources_unchanged"]
for name, digest in data["source_hashes"].items():
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
runs = data["runs"]
assert runs["core"]["end_ns"] <= runs["ui"]["start_ns"]
assert runs["ui"]["end_ns"] <= runs["combine"]["start_ns"]
assert runs["combine"]["exit"] == 0
assert "--keep" in runs["combine"]["command"]
assert len([x for x in runs["ui"]["command"] if x == "--deselect"]) == 1
assert len(next(x for x in runs["core"]["command"] if x.startswith("--basetemp=")).split("=", 1)[1]) > 100
for name in ("core", "ui"):
    assert "--cov-branch" in runs[name]["command"]
    assert "--cov-fail-under=0" in runs[name]["command"]
    assert not (OUT / (".coverage." + name)).exists()  # Recorded retention failure.
    root = ET.parse(OUT / (name + ".xml")).getroot()
    print(name, runs[name]["exit"], [s.attrib for s in root.iter("testsuite")])
assert (OUT / ".coverage").stat().st_size > 0
print("coverage report exit", runs["report"]["exit"])

runtime = json.loads((OUT / "coverage-runtime.json").read_text())
assert runtime["fail_under"] == 90
assert runtime["branch"] is True
assert runtime["source"] == ["src/aconsole"]
assert runtime["total"] >= 90
assert "keep=bool(options.keep_combined)" in runtime["command_line_source"]
print("Retention criterion FAILED: CLI report removed separate input datasets")
