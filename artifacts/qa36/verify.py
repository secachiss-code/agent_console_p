# ruff: noqa: INP001, S101, S314, T201
"""Check local regression evidence; never grant independent acceptance."""

import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
index = json.loads((HERE / "evidence.json").read_text())
for name, digest in index["sha256"].items():
    assert hashlib.sha256((HERE / name).read_bytes()).hexdigest() == digest, name
record = json.loads((HERE / "run-1/result.json").read_text())
assert record["state"] == "complete"
assert record["pytest_exit"] == 0
assert record["signals"] == []
assert record["timed_out"] is False
assert record["gil_enabled"] is False
assert record["source_unchanged"] is True
assert record["source_before"] == record["source_after"]
for name, digest in record["source_after"].items():
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
assert hashlib.sha256((HERE / "run-1/pytest.log").read_bytes()).hexdigest() == record["log_sha256"]
suite = ET.parse(HERE / "run-1/pytest.xml").getroot()[0]
assert {key: suite.get(key) for key in ("tests", "failures", "errors", "skipped")} == {
    "tests": "2218", "failures": "0", "errors": "0", "skipped": "1",
}
assert "QA36 sessionfinish: exitstatus=0, gil=False" in (HERE / "run-1/pytest.log").read_text()
print(json.dumps({"evidence_consistent": True, "pytest_exit": 0, "passed": 2217, "skipped": 1,
                  "source_unchanged": True, "historical_sigterm_cause": "not established",
                  "independent_security_review": False, "release_ready": False}))
