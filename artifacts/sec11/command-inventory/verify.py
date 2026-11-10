"""Recompute the static scan and check evidence, without executing product code."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
recorded = json.loads((HERE / "inventory.json").read_text())
scanner = runpy.run_path(str(HERE / "run.py"))
fresh = scanner["collect"]()
for key in ("source_hashes", "runner_imports", "calls"):
    assert recorded[key] == fresh[key], key
assert hashlib.sha256((HERE / "run.py").read_bytes()).hexdigest() == recorded["scanner_sha256"]
assert all(alias["alias"] is None for row in recorded["runner_imports"] for alias in row["names"])
old = json.loads((ROOT / "artifacts/sec11/hook-census/results.json").read_text())["inventory"]
old_counts = Counter((row["path"], row["receiver"] + "." + row["method"]) for row in old)
new_counts = Counter((row["path"], row["target"]) for row in recorded["calls"])
assert not (old_counts - new_counts), old_counts - new_counts
counts = Counter(row["kind"] for row in recorded["calls"])
assert counts["runner_construction"] == len(recorded["runner_imports"]) == 8
assert any(row["path"].endswith("clipboard.py") and row["kind"] == "runner_candidate"
           for row in recorded["calls"])
print(json.dumps({"evidence_integrity": "PASS", "files": len(recorded["source_hashes"]),
                  "calls": len(recorded["calls"]), "counts": dict(sorted(counts.items())),
                  "old_candidate_path_target_multiplicity_preserved": True,
                  "security_acceptance": "NOT RUN"}, indent=2))
