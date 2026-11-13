# ruff: noqa: INP001, S101, S314, T201, PLR2004
# Offline evidence verifier; assertions and stdout intentional, XML from local pytest.
"""Check provenance, dataset retention and exact combined branch union."""

import hashlib
import json
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/qa33"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def branches(path: Path) -> tuple[set[str], set[tuple[str, str, int, int]]]:
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT value FROM meta WHERE key='has_arcs'").fetchone() == ("1",)
        files = {row[0] for row in db.execute("SELECT path FROM file")}
        arcs = set(
            db.execute(
                "SELECT file.path, context.context, arc.fromno, arc.tono "
                "FROM arc JOIN file ON file.id=arc.file_id "
                "JOIN context ON context.id=arc.context_id"
            )
        )
    assert files
    assert arcs
    return files, arcs


data = json.loads((OUT / "results.json").read_text())
assert data["sources_unchanged"]
assert data["archives_retained"]
for group in ("source_hashes", "inputs"):
    for name, expected in data[group].items():
        assert digest(ROOT / name) == expected, name
runs = data["runs"]
assert list(runs) == ["core", "ui", "combine", "report"]
for previous, current in zip(list(runs), list(runs)[1:], strict=False):
    assert runs[previous]["end_ns"] <= runs[current]["start_ns"]
assert all(run["exit"] == 0 for run in runs.values())
assert "--keep" in runs["combine"]["command"]
assert "--keep-combined" in runs["report"]["command"]
assert runs["core"]["command"][3:13] == [
    "tests/core",
    "tests/contracts",
    "tests/gate",
    "tests/hostrun",
    "tests/smoke",
    "tests/test_package.py",
    "tests/test_packaging.py",
    "tests/test_contrast.py",
    "tests/test_build_guards.py",
    "tests/test_socket_paths.py",
]
assert runs["ui"]["command"][3:6] == ["tests/gui", "tests/ui", "tests/smoke"]
assert runs["ui"]["command"].count("--deselect") == 1
assert runs["ui"]["command"][runs["ui"]["command"].index("--deselect") + 1] == (
    "tests/gui/front/test_git_scale_ui.py::test_scrolling_a_long_history"
)
assert len(next(x.split("=", 1)[1] for x in runs["core"]["command"] if x.startswith("--basetemp="))) > 100
all_files: set[str] = set()
all_arcs: set[tuple[str, str, int, int]] = set()
for name in ("core", "ui"):
    assert "--cov-branch" in runs[name]["command"]
    assert "--cov-fail-under=0" in runs[name]["command"]
    item = data["archives"][name]
    assert item["sha256"] == item["original_sha256"] == digest(ROOT / item["path"])
    assert runs[name]["end_ns"] <= item["archived_ns"] < runs["combine"]["start_ns"]
    files, arcs = branches(ROOT / item["path"])
    all_files.update(files)
    all_arcs.update(arcs)
    suites = list(ET.parse(OUT / (name + ".xml")).getroot().iter("testsuite"))
    assert suites
    assert all(int(s.attrib.get("failures", "0")) == int(s.attrib.get("errors", "0")) == 0 for s in suites)
    print(name, [s.attrib for s in suites], "files", len(files), "arcs", len(arcs))
assert branches(OUT / ".coverage") == (all_files, all_arcs)
runtime = json.loads((OUT / "coverage-runtime.json").read_text())
assert runtime["fail_under"] == 90
assert runtime["branch"] is True
assert runtime["source"] == ["src/aconsole"]
assert runtime["total"] >= 90
manifest = json.loads((OUT / "evidence.json").read_text())
for name, expected in manifest.items():
    assert digest(OUT / name) == expected, name
print("Retained datasets match the exact combined branch union; coverage", runtime["total"])
