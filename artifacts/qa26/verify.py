"""Recompute finite suite outcomes and verify original source/test bytes."""

import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/qa26"
BASE = "63cec43f8813972940a42e97a6ddeb65eb811d16"
results = {}
for label in ("full-private", "desktop-scale", "desktop-full"):
    directory = OUT / label
    metadata = json.loads((directory / "run.json").read_text())
    suites = ET.parse(directory / "pytest.xml").getroot()
    cases = list(suites.iter("testcase"))
    failed = [
        case.attrib["classname"] + "::" + case.attrib["name"]
        for case in cases
        if case.find("failure") is not None or case.find("error") is not None
    ]
    skipped = sum(case.find("skipped") is not None for case in cases)
    assert metadata["returncode"] == (1 if failed else 0)
    results[label] = {
        "tests": len(cases),
        "passed": len(cases) - len(failed) - skipped,
        "failed": failed,
        "skipped": skipped,
        "returncode": metadata["returncode"],
    }
paths = subprocess.check_output(
    ["git", "ls-tree", "-r", "--name-only", BASE, "--", "src", "tests"], cwd=ROOT, text=True
).splitlines()
unchanged = hashlib.sha256()
for path in paths:
    expected = subprocess.check_output(["git", "show", BASE + ":" + path], cwd=ROOT)
    actual = (ROOT / path).read_bytes()
    assert actual == expected, path
    unchanged.update(path.encode() + b"\0" + hashlib.sha256(actual).digest())
results["preserved"] = {"base": BASE, "files": len(paths), "path_content_digest": unchanged.hexdigest()}
results["renderer"] = json.loads((OUT / "desktop-scale/renderer.json").read_text())
results["desktop_full_renderer"] = json.loads((OUT / "desktop-full/renderer.json").read_text())
results["scale_reports"] = {
    label: (OUT / label / "scale.txt").read_text().strip()
    for label in ("desktop-scale", "desktop-full")
}
results["evidence_hashes"] = {
    str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(OUT.rglob("*"))
    if path.is_file() and path.suffix in (".py", ".xml", ".log", ".txt", ".json") and path.name != "validation.json"
}
(OUT / "validation.json").write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps({key: value for key, value in results.items() if key != "evidence_hashes"}, indent=2))
