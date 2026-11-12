"""Check preserved XML outcomes, identity controls and unchanged source hashes."""

import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

root = Path(__file__).resolve().parents[2]
out = root / "artifacts/qa21"
result = {"base": "7d8e5cd37d97f1ac5fe88ce2327ea0c1ee3d3d02", "suites": {}, "identities": {}, "unchanged": {}}
for label, expected in [("desktop", (48, 11)), ("private", (48, 0))]:
    suites = ET.parse(out / (label + ".xml")).getroot().findall("testsuite")
    tests = sum(int(s.attrib["tests"]) for s in suites)
    failures = sum(int(s.attrib["failures"]) for s in suites)
    assert (tests, failures) == expected
    assert sum(int(s.attrib["errors"]) + int(s.attrib["skipped"]) for s in suites) == 0
    result["suites"][label] = {"tests": tests, "passed": tests - failures, "failed": failures}
for label, size in [
    ("identity-original-1", [1920, 1080]),
    ("identity-control", [700, 800]),
    ("identity-original-2", [1920, 1080]),
]:
    events = json.loads((out / (label + ".json")).read_text())
    assert [e["label"] for e in events] == ["first-paint", "settled"]
    assert all([e["width"], e["height"]] == size and e["default"] == size for e in events)
    assert all(not e["maximized"] and not e["fullscreen"] for e in events)
    result["identities"][label] = size
paths = subprocess.check_output(
    ["git", "ls-tree", "-r", "--name-only", result["base"], "src", "tests"], cwd=root, text=True
).splitlines()
for name in paths:
    if name.endswith((".py", ".png")):
        current = (root / name).read_bytes()
        baseline = subprocess.check_output(["git", "show", result["base"] + ":" + name], cwd=root)
        assert current == baseline, name
        result["unchanged"][name] = hashlib.sha256(current).hexdigest()
result["evidence"] = {
    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
    for p in sorted(out.iterdir())
    if p.suffix in (".xml", ".json", ".png", ".log") and p.name != "validation.json"
}
(out / "validation.json").write_text(json.dumps(result, indent=2) + "\n")
print("XML, identity controls and source/baseline hashes verified")
