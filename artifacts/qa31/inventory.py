"""Reproduce the QA31 evidence inventory without executing application code."""
# ruff: noqa: INP001 -- standalone evidence script, not an importable package

import hashlib
import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()  # noqa: S603, S607 -- fixed read-only Git commands


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    evidence = [
        "qa4-recheck/long-before.xml", "qa4-recheck/long-after.xml", "qa4-recheck/long-core.xml",
        "qa4-final/core.xml", "qa4-final/ui.xml", "qa4-final/coverage-gate.txt",
        "qa4-final/coverage-summary.json", "qa4-final/build-result.json", "qa4-final/build-source.json",
        "qa4-final/package-smoke.json", "qa4-final/ci-lookup.json", "qa4-recheck/ci43-result.log",
        "qa25/final-geometry/pytest.xml", "qa26/full-private/pytest.xml",
        "qa30/desktop-full/pytest.xml", "qa30/desktop-scale/pytest.xml",
    ]
    records = {}
    for relative in evidence:
        path = ROOT / "artifacts" / relative
        record = {"exists": path.is_file()}
        if path.is_file():
            record["sha256"] = sha(path)
            if path.suffix == ".xml":
                suites = list(ET.parse(path).getroot().iter("testsuite"))  # noqa: S314 -- local committed JUnit evidence
                record["junit"] = {
                    key: sum(int(suite.get(key, "0")) for suite in suites)
                    for key in ("tests", "failures", "errors", "skipped")
                }
        records["artifacts/" + relative] = record
    source_comparisons = {}
    for kind in ("core", "ui", "build"):
        source = json.loads((ROOT / f"artifacts/qa4-final/{kind}-source.json").read_text())
        source_comparisons[kind] = {
            "base": source["commit"],
            "snapshot_files": len(source["hashes"]),
            "changed_or_missing": [name for name, digest in source["hashes"].items()
                                   if not (ROOT / name).is_file() or sha(ROOT / name) != digest],
        }
    baseline = source_comparisons["build"]["base"]
    guarded = ["pyproject.toml", "uv.lock", "packaging", ".github", "tests/conftest.py",
               "tests/socket_paths.py", "tests/test_socket_paths.py", "tests/test_build_guards.py",
               "contracts", "tests/fixtures/contracts", "tests/contracts", "tools/check_contracts.py"]
    package = ROOT / "dist/aconsole_0.1.1_amd64.deb"
    payload = {
        "base_head": git("rev-parse", "HEAD"),
        "container_uid": os.getuid(),
        "container_os_release": Path("/etc/os-release").read_text(),
        "container_tools": {name: shutil.which(name) for name in
                            ("podman", "unshare", "bwrap", "proot", "chroot", "dpkg-deb", "apt-get")},
        "evidence": records,
        "qa17_source_comparisons": source_comparisons,
        "guarded_diff_from_qa17": git("diff", "--name-only", baseline, "HEAD", "--", *guarded),
        "product_diff_from_qa17": git("diff", "--name-only", baseline, "HEAD", "--", "src"),
        "tracked_golden_directories": sorted({str(Path(name).parent) for name in
                                               git("ls-files").splitlines() if "/golden/" in name}),
        "package": {"path": str(package.relative_to(ROOT)), "sha256": sha(package),
                    "bytes": package.stat().st_size},
        "current_inputs": {name: sha(ROOT / name) for name in
                           (".github/workflows/check.yml", "pyproject.toml", "packaging/build_deb.py",
                            "tests/conftest.py", "tests/socket_paths.py", "tests/test_socket_paths.py",
                            "tests/test_build_guards.py", "tools/check_contracts.py")},
        "limit": "Inventory only. Historical results retain their original revision and scope.",
    }
    (OUT / "inventory.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))  # noqa: T201 -- report CLI result


if __name__ == "__main__":
    main()
