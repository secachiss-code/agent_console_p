"""Recompute finite pair outcomes and verify unchanged implementation/evidence."""

import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/qa28"
BASE = "b61c6edd8251600a947e66535de48a06cdf8489f"
PRESERVED = ["src", "tests", "tools", "artifacts/qa26", "artifacts/qa27"]
subprocess.run(["git", "diff", "--exit-code", BASE, "--", *PRESERVED], cwd=ROOT, check=True)
results = {}
for label in ("desktop-full", "desktop-scale"):
    directory = OUT / label
    run = json.loads((directory / "run.json").read_text())
    environment = json.loads((directory / "environment.json").read_text())
    observation = json.loads((directory / "observation.json").read_text())
    cases = list(ET.parse(directory / "pytest.xml").getroot().iter("testcase"))
    failed = [case.attrib["classname"] + "::" + case.attrib["name"] for case in cases
              if case.find("failure") is not None or case.find("error") is not None]
    skipped = sum(case.find("skipped") is not None for case in cases)
    scale = [case for case in cases if case.attrib["name"] == "test_scrolling_a_long_history"]
    assert len(scale) == 1
    assert environment["head"] == BASE
    assert run["returncode"] == (1 if failed else 0)
    assert run["renderer_override"] is None
    assert run["production_identity_changed"] is False
    assert observation["rows"] == 10_000
    assert observation["source_enumeration_complete"] is False
    assert all(observation["allocation"][name]["width"] > 0 for name in ("window", "pane", "list"))
    line = (directory / "scale.txt").read_text().strip()
    match = re.fullmatch(
        r"git scale: (\d+) rows, (\d+) frames in (\d+) s, median ([\d.]+) ms, "
        r"p95 ([\d.]+) ms, worst ([\d.]+) ms, RSS (\d+) MB "
        r"\(start (\d+), first page (\d+), all rows (\d+) after ([\d.]+) s\)", line
    )
    assert match is not None
    assert match[1] == "10000" and match[3] == "4"
    if label == "desktop-full":
        previous = ET.parse(ROOT / "artifacts/qa27/full-desktop/pytest.xml").getroot()
        prior_failures = [case.attrib["classname"] + "::" + case.attrib["name"]
                          for case in previous.iter("testcase") if case.find("failure") is not None]
        assert failed == prior_failures
    results[label] = {
        "tests": len(cases), "passed": len(cases) - len(failed) - skipped,
        "failed": failed, "skipped": skipped, "returncode": run["returncode"],
        "scale_passed": scale[0].find("failure") is None and scale[0].find("error") is None,
        "scale_report": line, "window": observation["allocation"]["window"],
        "windows": len(observation["windows"]),
        "mapped_windows": sum(window["mapped"] for window in observation["windows"]),
        "tasks": len(observation["tasks"]), "sampled_sources": len(observation["sources"]),
    }
results["preserved"] = {"base": BASE, "paths": PRESERVED}
results["evidence_hashes"] = {
    str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(OUT.rglob("*"))
    if path.is_file() and path.suffix in (".py", ".xml", ".log", ".json", ".txt", ".md")
    and path.name != "validation.json"
}
(OUT / "validation.json").write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps({key: value for key, value in results.items() if key != "evidence_hashes"}, indent=2))
