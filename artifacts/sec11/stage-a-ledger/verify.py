"""Verify provenance and gate coverage; this is not independent acceptance."""

import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from collect import collect, SOURCES

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def require(value, message):
    if not value:
        raise ValueError(message)


def verify():
    evidence = json.loads((HERE / "evidence.json").read_text())
    ledger = json.loads((HERE / "ledger.json").read_text())
    require(evidence == collect(), "Evidence hashes/catalog drift; review before updating")
    base = ledger["base_commit"]
    require(len(base) == 40 and all(c in "0123456789abcdef" for c in base), "Invalid base SHA")
    # Pin the current product to a real commit, not just a self-consistent manifest.
    subprocess.run(["git", "merge-base", "--is-ancestor", base, "HEAD"], cwd=ROOT, check=True)
    for name, expected in evidence["product_source_hashes"].items():
        data = subprocess.check_output(["git", "show", f"{base}:{name}"], cwd=ROOT)
        require(hashlib.sha256(data).hexdigest() == expected, f"Base source mismatch: {name}")
    catalog = evidence["catalog"]
    require(set(catalog) == set(SOURCES) | {"GIT-6"}, "Missing evidence task")
    drift = {k: v["source_drift"] for k, v in catalog.items() if v["source_drift"]}
    require(drift == {"SEC-15": ["src/aconsole/core/infrastructure/git/operations.py"]}, "Unexpected historical drift")
    require(ledger["stage_A"] == "INCOMPLETE" and ledger["stage_B_authorized"] is False, "Unaccepted stage promotion")
    require(ledger["SEC11"] == ledger["SEC13"] == "failed", "Security blockers changed")
    require(ledger["security_acceptance"] == "NOT RUN", "Independent acceptance not provided")
    expected_ids = {"A-command", "A-commit", "A-merge", "A-refusal", "A-message", "A-index", "A-recovery", "A-phases", "A-helper", "A-origins"}
    criteria = ledger["criteria"]
    require(len(criteria) == len(expected_ids) and {r["id"] for r in criteria} == expected_ids, "Gate criteria missing/duplicated")
    referenced = set()
    for row in criteria:
        require(row["status"] in {"bounded_inventory", "measured_with_limits", "compatibility_difference", "partial"}, "Unknown acceptance state")
        require(row["finding"] and row["remaining"] and row["evidence"], "Missing finding/limit/evidence")
        for key in row["evidence"]:
            require(key in catalog, f"Unknown evidence {key}")
            referenced.add(key)
    require(referenced == set(catalog), "Unreconciled evidence task")
    decision_ids = {d["id"] for d in ledger["decisions"]}
    require(decision_ids == {"D-helper", "D-phases", "D-candidate", "D-post", "D-scope", "D-publication"}, "Missing compatibility decision")
    for decision in ledger["decisions"]:
        require(set(decision["criteria"]) <= expected_ids, "Unknown decision criterion")
        require(decision["state"] in {"unresolved", "existing_GIT6_policy_only", "design_requirement_unimplemented"}, "Unrecorded decision approval")
        require(decision["proposal"] and decision["acceptance"], "Missing decision action")
    phases = {}
    for key, entry in catalog.items():
        for phase in entry.get("observed_phases", []):
            phases.setdefault(phase, []).append(key)
    require(ledger["phase_evidence"] == phases, "Phase union not derived from raw evidence")
    require(set(phases) == {"pre-commit", "prepare-commit-msg", "commit-msg", "post-commit", "pre-merge-commit", "post-merge", "pre-rebase", "post-rewrite", "reference-transaction", "post-index-change", "post-checkout"}, "Unaccounted phase")
    helpers = {}
    for key, entry in catalog.items():
        for phase in entry.get("observed_helper_phases", []):
            helpers.setdefault(phase, []).append(key)
    require(ledger["helper_evidence"] == helpers, "Helper phase evidence mismatch")
    require(set(helpers) == {"clean", "smudge", "merge"}, "Unaccounted helper")
    runs = json.loads((HERE / "reverification.json").read_text())
    require(set(runs) == set(SOURCES) - {"SEC-15"}, "Missing fresh evidence verification")
    for key, result in runs.items():
        require(result["exit"] == 0 and not result["stderr"], f"Evidence verifier failed: {key}")
        require(result["command"][1] == f"artifacts/sec11/{SOURCES[key]}/verify.py", "Wrong verifier")
        require(json.loads(result["stdout"])["evidence_integrity"] == "PASS", f"No integrity result: {key}")
    # Historical GIT6 assertions are verified as saved evidence, not rerun tests.
    for name, tests, failures in (("before.xml", 5, 4), ("regression.xml", 5, 0), ("related.xml", 132, 0)):
        suite = ET.parse(ROOT / "artifacts/git6" / name).getroot().find("testsuite")
        require(suite is not None, "Missing JUnit suite")
        require(int(suite.attrib["tests"]) == tests and int(suite.attrib["failures"]) == failures and int(suite.attrib["errors"]) == 0, "GIT6 evidence changed")
    return {"evidence_integrity": "PASS", "base_commit": base,
            "product_sources": len(evidence["product_source_hashes"]),
            "pinned_artifacts": len(evidence["artifact_hashes"]),
            "evidence_tasks": len(catalog), "historical_source_drift": drift,
            "criteria": len(criteria), "phases": len(phases), "decisions": len(decision_ids),
            "fresh_evidence_verifiers": len(runs),
            "stage_A": ledger["stage_A"], "stage_B_authorized": False,
            "security_acceptance": "NOT RUN", "fresh_product_tests": "NOT RUN"}


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
