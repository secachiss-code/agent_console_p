"""Recompute QA29 outcomes, CSS scalars, preservation and evidence hashes."""

import ast
import datetime
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/qa29"
BASE = "07e4f2b3b0d90a6ad68a0dc38fa518c5f9533d72"
PRESERVED = ["src", "tools", "tests/gui/snapshots", "artifacts/qa26", "artifacts/qa27", "artifacts/qa28"]
subprocess.run(["git", "diff", "--exit-code", BASE, "--", *PRESERVED], cwd=ROOT, check=True)


def tree(path, previous=False):
    source = (subprocess.check_output(["git", "show", BASE + ":" + path], cwd=ROOT, text=True)
              if previous else (ROOT / path).read_text())
    return ast.parse(source)


def functions(module):
    return {node.name: ast.dump(node) for node in module.body if isinstance(node, ast.FunctionDef)}


scale_path = "tests/gui/front/test_git_scale_ui.py"
assert functions(tree(scale_path)) == functions(tree(scale_path, previous=True))
def constants(module):
    return [ast.dump(node) for node in module.body if isinstance(node, ast.AnnAssign)]

assert constants(tree(scale_path)) == constants(tree(scale_path, previous=True))
contrast_path = "tests/gui/front/test_contrast_table.py"
assert [ast.dump(node) for node in ast.walk(tree(contrast_path)) if isinstance(node, ast.Assert)] == [
    ast.dump(node) for node in ast.walk(tree(contrast_path, previous=True)) if isinstance(node, ast.Assert)]
conftest_path = "tests/gui/front/conftest.py"
assert functions(tree(conftest_path))["_release_test_windows"] == functions(
    tree(conftest_path, previous=True))["_release_test_windows"]

control = json.loads((OUT / "control.json").read_text())
assert control["bare"] == control["removed"]
assert [control[name]["list"]["height"] for name in ("bare", "styled", "removed")] == [766, 754, 766]
assert control["bare"]["toolbar"]["padding"] == [0, 0, 0, 0]
assert control["styled"]["toolbar"]["padding"] == [6, 16, 6, 16]
results = {"control": control, "preserved": {"base": BASE, "paths": PRESERVED}, "runs": {}}
timings = {}
for label, expected in [("control", (1, 0)), ("focused", (17, 2)), ("focused-fixed", (17, 0)), ("scale", (2, 0))]:
    directory = OUT / label
    suite = next(ET.parse(directory / "pytest.xml").getroot().iter("testsuite"))
    cases = list(suite.iter("testcase"))
    failures = [case.attrib["name"] for case in cases if case.find("failure") is not None]
    assert (len(cases), len(failures)) == expected
    assert suite.attrib["errors"] == suite.attrib["skipped"] == "0"
    run = json.loads((directory / "run.json").read_text())
    assert run["returncode"] == (1 if failures else 0)
    assert run["renderer_override"] is None and run["production_identity_changed"] is False
    start = datetime.datetime.fromisoformat(suite.attrib["timestamp"])
    timings[label] = (start, start + datetime.timedelta(seconds=float(suite.attrib["time"])))
    results["runs"][label] = {"tests": len(cases), "failures": failures, "returncode": run["returncode"],
                               "duration": suite.attrib["time"]}
    if label != "control":
        assert json.loads((directory / "environment.json").read_text())["head"] == BASE
results["focused_scale_overlap_seconds"] = max(
    0.0, (min(timings["focused-fixed"][1], timings["scale"][1])
          - max(timings["focused-fixed"][0], timings["scale"][0])).total_seconds())
results["scale_report"] = (OUT / "scale/scale.txt").read_text().strip()
results["performance_acceptance_claimed"] = False
results["evidence_hashes"] = {
    str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(OUT.rglob("*"))
    if path.is_file() and path.suffix in (".py", ".xml", ".log", ".json", ".txt", ".md")
    and path.name != "validation.json"
}
(OUT / "validation.json").write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps({key: value for key, value in results.items() if key != "evidence_hashes"}, indent=2))
