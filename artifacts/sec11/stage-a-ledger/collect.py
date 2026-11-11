"""Index existing evidence only; never execute Git mutations or product imports."""

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCES = {
    "SEC-15": "hook-census", "SEC-16": "hook-census-extended",
    "SEC-17": "command-inventory", "SEC-18": "sequencer-census",
    "SEC-19": "worktree-checkpoint-census", "SEC-20": "helper-census",
    "SEC-21": "repository-origins", "SEC-22": "sequencer-edits",
    "SEC-23": "sequencer-rpc", "SEC-24": "merge-refusal-rpc",
    "SEC-25": "sequencer-refusal-rpc", "SEC-26": "worktree-checkpoint-rpc",
    "SEC-27": "helper-rpc", "SEC-28": "repository-origins-rpc",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def events(value):
    """Find recorded phase objects regardless of case/step trace shape."""
    if isinstance(value, dict):
        if isinstance(value.get("phase"), str):
            yield value["phase"]
        for item in value.values():
            yield from events(item)
    elif isinstance(value, list):
        for item in value:
            yield from events(item)


def snapshot(data):
    hashes = data.get("source_hashes", {})
    return {
        "recorded_source_count": len(hashes),
        "source_drift": [
            name for name, expected in sorted(hashes.items())
            if not (ROOT / name).is_file() or digest(ROOT / name) != expected
        ],
    }


def collect():
    catalog = {}
    artifacts = {}
    for key, directory in SOURCES.items():
        folder = ROOT / "artifacts/sec11" / directory
        filename = "inventory.json" if key == "SEC-17" else "results.json"
        data = json.loads((folder / filename).read_text())
        phases = set(events(data.get("cases", [])))
        helper_phases = phases & {"clean", "smudge", "merge"}
        catalog[key] = {
            "directory": str(folder.relative_to(ROOT)),
            "base_commit": data["base_commit"],
            "cases": len(data.get("cases", [])),
            "observed_phases": sorted(phases - helper_phases),
            "observed_helper_phases": sorted(helper_phases),
            **snapshot(data),
        }
        for name in ("REPORT.md", filename, "comparison.json", "validation.json", "run.py", "verify.py"):
            path = folder / name
            if path.is_file():
                artifacts[str(path.relative_to(ROOT))] = digest(path)
    git6 = ROOT / "artifacts/git6"
    data = json.loads((git6 / "regression-result.json").read_text())
    catalog["GIT-6"] = {"directory": "artifacts/git6", "base_commit": data["base"], **snapshot(data)}
    for name in ("REPORT.md", "validation.json", "regression-result.json", "related-result.json", "before.xml", "regression.xml", "related.xml"):
        path = git6 / name
        artifacts[str(path.relative_to(ROOT))] = digest(path)
    for name in ("artifacts/sec11/architecture/DESIGN.md", "artifacts/sec11/architecture/evidence.json"):
        artifacts[name] = digest(ROOT / name)
    product = {str(p.relative_to(ROOT)): digest(p) for p in sorted((ROOT / "src").rglob("*.py"))}
    return {"schema": 1, "product_source_hashes": product, "catalog": catalog, "artifact_hashes": artifacts}


if __name__ == "__main__":
    # Write to stdout so a reviewer can compare without modifying pinned evidence.
    print(json.dumps(collect(), indent=2, sort_keys=True))
