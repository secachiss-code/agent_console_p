"""Verify the finite QA-23 evidence and preservation of existing assertions."""

import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "artifacts/qa23"
BASE = "d0ce1d87a5d1c5d6aac0d6ba0719f65093392725"
results = {}
for name, expected in {
    "native-green": (20, 2),
    "native-green-2": (21, 0),
    "desktop-selection": (56, 9),
    "native-final": (22, 0),
}.items():
    cases = ET.parse(OUTPUT / (name + ".xml")).findall(".//testcase")
    failed = [case.get("name") for case in cases if case.find("failure") is not None]
    assert (len(cases), len(failed)) == expected
    assert all(case.find("error") is None and case.find("skipped") is None for case in cases)
    results[name] = {"total": len(cases), "failed": failed}
assert all("opening" not in name and "rings_withdraws" not in name
           for name in results["desktop-selection"]["failed"])
assert "All checks passed!" in (OUTPUT / "lint.log").read_text()
assert "0 errors, 0 warnings" in (OUTPUT / "types.log").read_text()
assert json.loads((OUTPUT / "geometry-after.json").read_text())["matches_qa22"]
test_path = "tests/gui/front/test_approval_alerts.py"
original = subprocess.check_output(["git", "show", BASE + ":" + test_path], cwd=ROOT)
assert (ROOT / test_path).read_bytes().split(b"\n\nclass _OpeningApp")[0] == original
changed = subprocess.check_output(["git", "diff", BASE, "--name-only", "--", "src", "tests"], cwd=ROOT)
assert set(changed.decode().splitlines()) == {"src/aconsole/ui/views/window.py", test_path}
hashes = {}
for path in [ROOT / "src/aconsole/ui/views/window.py", ROOT / test_path, *sorted((OUTPUT / "ui").glob("*.png"))]:
    hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
assert len(hashes) == 5
(OUTPUT / "validation.json").write_text(json.dumps({
    "base": BASE, "results": results, "hashes": hashes,
    "qa4_accepted": False, "performance_accepted": False, "security_accepted": False,
}, indent=2) + "\n")
print("QA-23 evidence verified")
