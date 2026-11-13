"""Read-only reconciliation of SEC34 provenance; no runtime or approval authority."""

import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PACKET = ROOT / "artifacts/sec11/worker-review-gate"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def reconcile() -> dict[str, object]:
    index = json.loads((PACKET / "source-index.json").read_bytes())
    base = index["base_commit"]
    changes = []
    counts = {}
    for group in ("product_source_hashes", "upstream_hashes"):
        historical = index[group]
        current = (
            {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in sorted((ROOT / "src").rglob("*.py"))}
            if group == "product_source_hashes"
            else {p: sha((ROOT / p).read_bytes()) for p in historical}
        )
        counts[group] = len(historical)
        for path, expected in historical.items():
            if sha(git("show", f"{base}:{path}")) != expected:
                raise ValueError(f"Historical base mismatch: {path}")
        for path in sorted(historical.keys() | current.keys()):
            if historical.get(path) != current.get(path):
                changes.append({"group": group, "path": path,
                                "historical": historical.get(path), "current": current.get(path)})
    all_changed = git("diff", "--name-only", base, "HEAD", "--", "src").decode().splitlines()
    patch = git("diff", "--unified=0", base, "HEAD", "--", "src")
    run = subprocess.run(
        ["python3", "-B", str(PACKET / "verify.py")], cwd=ROOT,
        capture_output=True, check=False,
    )
    # Exact historical script and its red result are retained, never rewritten.
    stderr = run.stderr.replace(str(ROOT).encode(), b"<ROOT>")
    expected_paths = {
        "src/aconsole/ui/bootstrap.py", "src/aconsole/ui/views/review/git_pane.py",
        "src/aconsole/ui/views/setup/preferences.py", "src/aconsole/ui/views/window.py",
    }
    if {row["path"] for row in changes} != expected_paths:
        raise ValueError("Unexpected provenance changes; review required")
    if any(row["group"] != "product_source_hashes" for row in changes):
        raise ValueError("Upstream evidence changed")
    if set(all_changed) != expected_paths | {"src/aconsole/ui/style/style.css"}:
        raise ValueError("Unexpected product diff")
    if run.returncode != 1 or run.stdout or b"Source/evidence index drift" not in stderr:
        raise ValueError("Historical verifier result changed")
    if patch != (HERE / "product.patch").read_bytes():
        raise ValueError("Product patch mismatch")
    if run.stdout != (HERE / "baseline.stdout").read_bytes() or stderr != (HERE / "baseline.stderr").read_bytes():
        raise ValueError("Baseline capture mismatch")
    return {
        "schema": 1, "head": git("rev-parse", "HEAD").decode().strip(),
        "historical_base": base, "historical_hashes_match_base": True,
        "historical_counts": counts, "changes": changes,
        "changed_product_paths": all_changed, "patch_sha256": sha(patch),
        "packet_hashes": {p.name: sha(p.read_bytes()) for p in sorted(PACKET.iterdir()) if p.is_file()},
        "baseline": {"command": "python3 -B artifacts/sec11/worker-review-gate/verify.py",
                     "exit": run.returncode, "stdout_sha256": sha(run.stdout),
                     "stderr_sha256": sha(stderr), "stderr_root_normalized": True},
        "core_diff": False, "upstream_diff": False, "actual_review": "NOT PERFORMED",
        "stage_B_authorized": False, "installed_acceptance": "NOT RUN",
        "SEC11": "failed", "SEC13": "failed",
    }


if __name__ == "__main__":
    result = reconcile()
    saved = json.loads((HERE / "results.json").read_bytes())
    # A later artifact-only commit changes HEAD, not the reviewed product bytes.
    recorded_head = saved.pop("head")
    result.pop("head")
    subprocess.run(["git", "merge-base", "--is-ancestor", recorded_head, "HEAD"], cwd=ROOT, check=True)
    if result != saved:
        raise ValueError("Reconciliation evidence drift")
    print(json.dumps({"reconciliation_integrity": "PASS", "historical_verifier_exit": 1,
                      "changed_python_files": 4, "additional_css_files": 1,
                      "actual_review": "NOT PERFORMED", "stage_B_authorized": False}, sort_keys=True))
