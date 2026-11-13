"""Recompute finite pair outcomes and verify unchanged implementation/evidence."""

import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/qa30"
BASE = "97cbf6c948ecdc0f61ee2f45c8b16db8a3a0fa7a"
PRESERVED = ["src", "tests", "tools", "artifacts/qa26", "artifacts/qa27", "artifacts/qa28", "artifacts/qa29"]
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
    expected_targets = (["tests/gui", "tests/ui", "tests/smoke"] if label == "desktop-full"
                        else ["tests/gui/front/test_git_scale_ui.py::test_scrolling_a_long_history"])
    assert run["command"][1:7] == ["-m", "pytest", "-o", "addopts=", "-q",
                                     "--junitxml=" + str(directory / "pytest.xml")]
    assert run["command"][7:] == ["-p", "observe", *expected_targets]
    assert run["gdk_backend"] == "x11" and run["display"] == ":0"
    assert environment["environment"] == json.loads(
        (ROOT / "artifacts/qa28/desktop-full/environment.json").read_text())["environment"]
    assert observation["renderer"] == "GskNglRenderer"
    assert observation["rows"] == 10_000
    assert environment["diff"] == ""
    assert observation["allocation"]["toolbar"]["padding"] == [6, 16, 6, 16]
    assert all(observation["allocation"][name]["width"] > 0 for name in ("window", "pane", "list"))
    line = (directory / "scale.txt").read_text().strip()
    match = re.fullmatch(
        r"git scale: (\d+) rows, (\d+) frames in (\d+) s, median ([\d.]+) ms, "
        r"p95 ([\d.]+) ms, worst ([\d.]+) ms, RSS (\d+) MB "
        r"\(start (\d+), first page (\d+), all rows (\d+) after ([\d.]+) s\)", line
    )
    assert match is not None
    assert match[1] == "10000" and match[3] == "4"
    prior_failures = json.loads((ROOT / "artifacts/qa28/validation.json").read_text())["desktop-full"]["failed"]
    results[label] = {
        "tests": len(cases), "passed": len(cases) - len(failed) - skipped,
        "failed": failed, "skipped": skipped, "returncode": run["returncode"],
        "scale_passed": scale[0].find("failure") is None and scale[0].find("error") is None,
        "scale_report": line, "window": observation["allocation"]["window"],
        "allocation": observation["allocation"],
        "new_failures": [failure for failure in failed if failure not in prior_failures],
        "prior_failures_absent": [failure for failure in prior_failures if failure not in failed]
                                 if label == "desktop-full" else [],
    }
full_interval = json.loads((OUT / "desktop-full/interval.json").read_text())
scale_interval = json.loads((OUT / "desktop-scale/interval.json").read_text())
for clock in ("wall_ns", "monotonic_ns"):
    assert full_interval["started"][clock] < full_interval["ended"][clock]
    assert full_interval["ended"][clock] < scale_interval["started"][clock]
    assert scale_interval["started"][clock] < scale_interval["ended"][clock]
assert results["desktop-full"]["allocation"] == results["desktop-scale"]["allocation"]
results["pair_gap_seconds"] = (scale_interval["started"]["monotonic_ns"]
                               - full_interval["ended"]["monotonic_ns"]) / 1e9
results["preserved"] = {"base": BASE, "paths": PRESERVED}
results["evidence_hashes"] = {
    str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(OUT.rglob("*"))
    if path.is_file() and path.suffix in (".py", ".xml", ".log", ".json", ".txt", ".md")
    and path.name != "validation.json"
}
(OUT / "validation.json").write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps({key: value for key, value in results.items() if key != "evidence_hashes"}, indent=2))
