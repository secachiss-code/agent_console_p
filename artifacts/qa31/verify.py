"""Check recorded QA31 evidence; never run tests or remote actions."""
# ruff: noqa: INP001 -- standalone evidence script, not an importable package
# ruff: noqa: S101, PLR2004 -- assertions compare the exact recorded evidence values

import hashlib
import json
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    inventory = json.loads((OUT / "inventory.json").read_text())
    for name, record in inventory["evidence"].items():
        assert record["exists"], name
        assert digest(ROOT / name) == record["sha256"], name
    for name, expected in inventory["current_inputs"].items():
        assert digest(ROOT / name) == expected, name
    assert inventory["guarded_diff_from_qa17"] == ""
    assert inventory["tracked_golden_directories"] == ["tests/fixtures/contracts/golden"]
    assert len(inventory["product_diff_from_qa17"].splitlines()) == 6
    for record in inventory["qa17_source_comparisons"].values():
        assert len(record["changed_or_missing"]) == 5
        assert record["snapshot_files"] == 280
    package = inventory["package"]
    previous = json.loads((ROOT / "artifacts/qa4-final/package-smoke.json").read_text())
    assert package["sha256"] == previous["sha256"] == digest(ROOT / package["path"])
    coverage = json.loads((ROOT / "artifacts/qa4-final/coverage-summary.json").read_text())
    assert coverage["percent_covered"] == 90.01828457446808
    host = json.loads((OUT / "host-environment.json").read_text())
    assert host["namespace_capability"]["exit_code"] == 1
    assert host["podman_info"]["exit_code"] == 0
    assert host["podman_info"]["selected_fields"]["rootless"] is True
    assert host["podman_images"]["exit_code"] == 0
    assert host["podman_images"]["ubuntu_24_04_candidates"] == []
    ci = json.loads((OUT / "ci-lookup.json").read_text())
    assert ci["commit"] == inventory["base_head"]
    responses = [result["value"] for result in ci["results"]]
    assert responses[0]["isError"] is True
    assert "422" in responses[0]["content"][0]["text"]
    assert responses[1]["structuredContent"]["statuses"] == []
    assert responses[2]["structuredContent"]["workflow_runs"] == []
    changed = subprocess.check_output(  # noqa: S603 -- fixed read-only Git diff
        ["git", "diff", inventory["base_head"], "--name-only", "--", "src", "tests", "tools", "packaging",  # noqa: S607 -- Git by name
         ".github", "pyproject.toml", "artifacts/qa30"], cwd=ROOT, text=True,
    )
    assert not changed, changed
    files = sorted(path for path in OUT.iterdir()
                   if path.is_file() and path.name != "validation.json")
    result = {"result": "PASS", "scope": "Recorded inventory consistency only; QA4 remains failed",
              "base_head": inventory["base_head"],
              "sha256": {path.name: digest(path) for path in files}}
    (OUT / "validation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))  # noqa: T201 -- report CLI result


if __name__ == "__main__":
    main()
