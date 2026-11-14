# ruff: noqa: ANN001, ANN201, ICN001, INP001, PLR2004, S101, S314, T201
# Standalone evidence verifier; local fixtures and explicit assertions are intentional.
"""Verify a consistent evidence package; never upgrade its failed acceptance."""

import gzip
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/qa6"


def load(name):
    return json.loads((OUT / name).read_text())


def suite(name):
    return ElementTree.parse(OUT / name).getroot()[0].attrib


original = suite("core.xml")
assert int(original["failures"]) == 1
assert "test_ingest_overflow_is_reported_after_latest_state_and_resyncs_the_ui" in (OUT / "core.xml").read_text()
assert int(suite("core-final.xml")["failures"]) == 0
assert load("collector-interruption.json")["collector_exit"] == 143

final = suite("core-verified.xml")
assert int(final["failures"]) == int(final["errors"]) == 0
assert int(final["tests"]) == int(original["tests"]) + 3
assert int(final["skipped"]) == 1
record = load("core-verified-result.json")
assert record["state"] == "running"
assert record["gil_enabled"] is False
recovery = load("core-verified-recovery.json")
assert recovery["outer_collector_exit"] == 143
assert recovery["pytest_exit"] is None
assert recovery["source_unchanged"] is True
assert record["source_before"] == recovery["source_after"]
for name, digest in recovery["source_after"].items():
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name

assert int(suite("focused-fix.xml")["failures"]) == 0
ui = suite("ui/pytest.xml")
assert int(ui["tests"]) == 65
assert int(ui["failures"]) == int(ui["errors"]) == 0
assert load("ui/run.json")["returncode"] == 0
assert load("scoped-lint-final.json") == []
lint = load("root-lint-final-summary.json")
assert lint["findings"] == 6193
assert lint["new_scope_findings"] == 0
assert load("legacy-lint-summary.json")["all_legacy_files_unchanged"] is True

security = load("security.json")
for row in security["ipc"]:
    assert row["initialize"]["message"] == ("SIDECAR_TOKEN_INVALID" if row["role"] == "sidecar" else "ROLE_FORBIDDEN")
    assert all(call["error"]["message"] == "NOT_INITIALIZED" for call in row["methods"])
for row in security["files"]:
    assert row["read_write_open"] is True
    assert row["bytes_changed_by_probe"] is False
    assert row["size_before"] == row["size_after"]
    assert row["mtime_before"] == row["mtime_after"]

for row in load("matrix.json").values():
    for name in row["evidence"]:
        assert (ROOT / name).is_file(), name
for name, digest in load("evidence-index.json")["sha256"].items():
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name

with gzip.open(OUT / "check-network.log.gz", "rt") as stream:
    checks = stream.read()
assert "0 errors, 0 warnings, 0 notes" in checks
assert "check_contracts: 11 checks passed" in checks
assert "api_floor_check: clean" in checks
assert load("summary.json")["release_ready"] is False
assert load("summary.json")["acceptance"] == "failed"
result = {"evidence_consistent": True, "acceptance": "failed", "release_ready": False,
          "core_junit_passed": int(final["tests"]) - int(final["skipped"]), "core_skipped": 1, "gtk_passed": 65,
          "core_os_exit_confirmed": False,
          "failed_acceptance": ["Cursor tool baseline missing", "host files open read-write",
                                "independent security review missing", "full collector terminated143"]}
(OUT / "verification-result.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
