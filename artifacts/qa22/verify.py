"""Check recorded geometry, restoration, pytest outcomes and source preservation."""

import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

root = Path(__file__).resolve().parents[2]
output = root / "artifacts/qa22"
base = "99b278cd9f4b70729aa409872a848f2835a5034f"
geometry = {}
for label, size in [("normal", [1920, 1080]), ("fixed", [700, 800]),
                    ("control", [700, 800]), ("normal-repeat", [700, 800]),
                    ("fixed-restore", [1920, 1080]), ("normal-restored", [1920, 1080])]:
    data = json.loads((output / (label + ".json")).read_text())
    assert len(data["events"]) == 2
    assert all(event["size"] == size for event in data["events"])
    assert all(not event["maximized"] and not event["fullscreen"] for event in data["events"])
    geometry[label] = size
original = json.loads((output / "normal.json").read_text())["database_before"]
restored = json.loads((output / "normal-restored.json").read_text())["database_after"]
assert original == restored
assert original == json.loads((output / "environment.json").read_text())["app_records_after_full_trace"]
outcomes = {}
for label, total, failures in [("desktop-trace", 2, 1), ("desktop-full-trace", 48, 11)]:
    cases = ET.parse(output / (label + ".xml")).findall(".//testcase")
    failed = [case.get("name") for case in cases if case.find("failure") is not None]
    assert len(cases) == total and len(failed) == failures
    assert not any(case.find("error") is not None or case.find("skipped") is not None for case in cases)
    outcomes[label] = dict(passed=total - failures, failures=failed)
trace = [json.loads(line) for line in (output / "desktop-trace.jsonl").read_text().splitlines()]
posted = next(event for event in trace if event["method"] == "_post_alert" and event["phase"] == "before")
assert posted["mapped"] and not posted["active"] and posted["width"] == posted["height"] == 0
activated = next(event for event in trace if event["method"] == "_activity_changed" and event["active"])
assert posted["seconds"] < activated["seconds"]
subprocess.run(["git", "diff", "--exit-code", base, "--", "src", "tests"], cwd=root, check=True)
tracked = subprocess.check_output(["git", "ls-files", "-z", "src", "tests"], cwd=root).decode().split("\0")
hashes = {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in tracked if path}
report = dict(base=base, geometry=geometry, original_app_records_restored=len(original),
              outcomes=outcomes, source_and_test_sha256=hashes)
(output / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
print("PASS: geometry controls, restored records, original failure counts, notification race, unchanged src/tests")
