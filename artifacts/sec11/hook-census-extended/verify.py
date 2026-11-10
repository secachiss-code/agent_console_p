"""Verify saved evidence consistency; this is not security acceptance."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
report = json.loads((HERE / "results.json").read_text())
comparisons = json.loads((HERE / "comparison.json").read_text())
cases = report["cases"]
operations = {"root", "amend", "merge", "ref_merge", "squash", "ref_squash", "fast_forward"}
modes = {"observe", "pre_refuse", "prepare_refuse", "post_fail", "ref_prepared", "ref_committed"}
expected = {(engine, operation, mode) for engine in ("native", "product")
            for operation in operations for mode in modes}
assert len(cases) == 84
assert {(c["engine"], c["operation"], c["mode"]) for c in cases} == expected
assert len(comparisons) == 42
assert len({c["sample_sha256"] for c in cases}) == 1
for relative, expected_hash in report["source_hashes"].items():
    assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected_hash, relative
for native, product, row in zip(cases[::2], cases[1::2], comparisons, strict=True):
    assert native["engine"] == "native" and product["engine"] == "product"
    assert native["operation"] == product["operation"] == row["operation"]
    assert native["mode"] == product["mode"] == row["mode"]
    assert row["outcome_equal"] == (native["ok"] == product["ok"])
    assert row["tree_equal"] == (native["after"]["target_tree"] == product["after"]["target_tree"])
    assert row["message_equal"] == (native["after"]["target_message"] == product["after"]["target_message"])
    assert row["phases_equal"] == ([(e["phase"], e["args"]) for e in native["events"]]
                                    == [(e["phase"], e["args"]) for e in product["events"]])
for case in cases:
    before, after = case["before"], case["after"]
    assert case["target_changed"] == (before["target"] != after["target"])
    assert case["index_bytes_unchanged"] == (before["index_sha256"] == after["index_sha256"])
    assert case["worktree_files_unchanged"] == (before["files"] == after["files"])
    assert before["files"]["untracked.txt"] == after["files"]["untracked.txt"]
    assert before["files"]["ignored.txt"] == after["files"]["ignored.txt"]
print(json.dumps({"evidence_integrity": "PASS", "cases": len(cases),
                  "source_hashes": len(report["source_hashes"]),
                  "equal_pairs": {key: sum(row[key] for row in comparisons)
                                  for key in ("outcome_equal", "tree_equal", "message_equal", "phases_equal")},
                  "security_acceptance": "NOT RUN"}, indent=2))
