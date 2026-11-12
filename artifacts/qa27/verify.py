"""Recompute outcomes and pin unchanged source, assertions, deadlines and QA26 evidence."""

import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/qa27"
BASE = "4b7a426c8e06e81e742fe430a2062efbbb52ca7e"
TEST = "tests/gui/front/test_git_actions.py"
results = {}
for label in ("targeted-desktop", "full-private", "full-desktop"):
    directory = OUT / label
    metadata = json.loads((directory / "run.json").read_text())
    cases = list(ET.parse(directory / "pytest.xml").getroot().iter("testcase"))
    failed = [
        case.attrib["classname"] + "::" + case.attrib["name"]
        for case in cases
        if case.find("failure") is not None or case.find("error") is not None
    ]
    skipped = sum(case.find("skipped") is not None for case in cases)
    assert metadata["returncode"] == (1 if failed else 0)
    assert not any("test_git_actions" in name for name in failed)
    results[label] = {
        "tests": len(cases),
        "passed": len(cases) - len(failed) - skipped,
        "failed": failed,
        "skipped": skipped,
        "returncode": metadata["returncode"],
    }

paths = subprocess.check_output(
    ["git", "ls-tree", "-r", "--name-only", BASE, "--", "src", "tests", "artifacts/qa26"],
    cwd=ROOT,
    text=True,
).splitlines()
digest = hashlib.sha256()
for path in paths:
    expected = subprocess.check_output(["git", "show", BASE + ":" + path], cwd=ROOT)
    actual = (ROOT / path).read_bytes()
    if path == TEST:
        text = actual.decode().replace('"--no-optional-locks", "status"', '"status"')
        text = text.replace("            # Observing completion must not compete with abort for the index lock.\n", "")
        text = text.replace(
            'lambda: (\n                    _git(tree, "status", "--porcelain") == ""\n'
            '                    and _git(tree, "rev-parse", "HEAD") == before\n                )',
            'lambda: _git(tree, "status", "--porcelain") == "" and _git(tree, "rev-parse", "HEAD") == before',
        )
        # The pre-existing exception diagnostic already used this option.
        assert text.encode() == expected
    else:
        assert actual == expected, path
    digest.update(path.encode() + b"\0" + hashlib.sha256(actual).digest())
controls = json.loads((OUT / "controls.json").read_text())
assert {record["algorithm"] for record in controls if record.get("passed")} == {"sha1", "sha256"}
results["preserved"] = {
    "base": BASE,
    "files_checked": len(paths),
    "path_content_digest": digest.hexdigest(),
    "only_test_change": "optional locks disabled in three read-only status calls; comment and wrapping",
}
results["evidence_hashes"] = {
    str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted(OUT.rglob("*"))
    if path.is_file() and path.suffix in (".py", ".xml", ".log", ".json", ".md") and path.name != "validation.json"
}
(OUT / "validation.json").write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps({key: value for key, value in results.items() if key != "evidence_hashes"}, indent=2))
