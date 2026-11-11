"""Read-only provenance collection; never imports product or launches a worker."""

import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = "fea8c49c3604bde3e5fdb8fd2e2fa263970aec03"
FOLDERS = (
    "stage-a-ledger", "stage-a-contract", "candidate-contract-fixtures",
    "candidate-snapshot-import", "candidate-result-contract", "architecture",
)
PROBES = (
    "artifacts/sec11/race_probe.py",
    "artifacts/sec11/config-namespace/test_snapshot_races.py",
    "artifacts/sec11/config-namespace/launcher.py",
    "artifacts/sec11/config-namespace/namespace_plugin.py",
    "artifacts/sec11/config-namespace/run.py",
    "artifacts/sec11/config-namespace/results.json",
    "artifacts/sec11/config-namespace/adversarial-final.xml",
    "artifacts/sec11/config-namespace/REPORT.md",
    "tests/core/git_helpers.py",
    "tests/core/test_git_host_exec.py",
    "tests/core/test_git_security_environment.py",
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def collect():
    tracked = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", BASE], cwd=ROOT).decode().splitlines()
    upstream = sorted(set(PROBES) | {
        p for p in tracked if any(p.startswith(f"artifacts/sec11/{folder}/") for folder in FOLDERS)
    })
    return {
        "schema": 1,
        "base_commit": BASE,
        "product_source_hashes": {
            str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted((ROOT / "src").rglob("*.py"))
        },
        "upstream_hashes": {p: digest((ROOT / p).read_bytes()) for p in upstream},
        "probe_sources": list(PROBES),
        "evidence_freshness": "Historical local evidence pinned to bytes; no installed rerun or independent review",
    }


if __name__ == "__main__":
    print(json.dumps(collect(), indent=2, sort_keys=True))
