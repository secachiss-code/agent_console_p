# ruff: noqa: C901, EM101, EM102, INP001, S603, S607, T201, TRY003
# Standalone diagnostic uses Git plumbing and explicit transport validation errors.
"""Export committed review inputs. Integrity is transport evidence, never acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

SCOPE = (
    "src", "tests", "tools", "contracts", "packaging", "migrations", ".github", "plans",
    "pyproject.toml", "uv.lock", "README.md", "PROJECT.md", "TECH_DEBT.md", "ISSUES.md",
    "CHANGELOG.md", "install.sh", "LICENSE", ".gitignore",
)


def git(root: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], input=data, capture_output=True, check=True, timeout=60,
    ).stdout


def prepare(root: Path, output: Path, revision: str = "HEAD") -> dict[str, object]:
    head = git(root, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}").decode().strip()
    tree = git(root, "rev-parse", f"{head}^{{tree}}").decode().strip()
    entries = []
    for record in git(root, "ls-tree", "-rz", head, "--", *SCOPE).split(b"\0"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split()
        path = name.decode("utf-8")
        parts = PurePosixPath(path)
        if (mode not in {"100644", "100755"} or kind != "blob" or parts.is_absolute()
                or ".." in parts.parts or "\\" in path or str(parts) != path):
            raise ValueError(f"Unsupported review input: {path!r} ({mode} {kind})")
        entries.append((path, mode, oid))
    if not entries:
        raise ValueError("Empty review scope")
    # Read immutable objects, without worktree filters, export-ignore or export-subst.
    batch = git(root, "cat-file", "--batch", data=b"".join(oid.encode() + b"\n" for _, _, oid in entries))
    offset = 0
    files = {}
    contents = []
    for path, mode, expected_oid in entries:
        header_end = batch.index(b"\n", offset)
        oid, kind, size_text = batch[offset:header_end].decode("ascii").split()
        size = int(size_text)
        offset = header_end + 1
        content = batch[offset:offset + size]
        offset += size + 1
        if oid != expected_oid or kind != "blob" or len(content) != size or batch[offset - 1:offset] != b"\n":
            raise ValueError("Invalid Git object response")
        files[path] = {"git_oid": oid, "git_mode": mode, "size": size, "sha256": hashlib.sha256(content).hexdigest()}
        contents.append((path, mode, content))
    if offset != len(batch):
        raise ValueError("Unexpected Git object response trailer")
    output.mkdir(parents=True, exist_ok=False)
    archive = output / "candidate.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path, mode, content in contents:
            info = zipfile.ZipInfo("source/" + path, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = int(mode, 8) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, content)
    # Read back the actual deliverable, rather than just hashing the input buffer.
    with zipfile.ZipFile(archive) as bundle:
        if bundle.namelist() != ["source/" + path for path, _, _ in entries]:
            raise ValueError("Archive file set mismatch")
        for path, mode, _ in entries:
            info = bundle.getinfo("source/" + path)
            if (info.external_attr >> 16 != int(mode, 8)
                    or hashlib.sha256(bundle.read(info)).hexdigest() != files[path]["sha256"]):
                raise ValueError("Archive content mismatch")
    result: dict[str, object] = {
        "schema": "aconsole-review-candidate-v1", "acceptance": "UNVERIFIED",
        "head": head, "tree": tree, "scope": SCOPE,
        "excludes": ["artifacts", "untracked files", "worktree/index changes", "external docs", "runtime state"],
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "files": files,
        "trust": "Agent-produced transport. Independent reviewer must pin expected revision and verify independently.",
    }
    (output / "candidate.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--revision", default="HEAD")
    args = parser.parse_args()
    result = prepare(Path(__file__).resolve().parents[2], args.output, args.revision)
    print(json.dumps({key: result[key] for key in ("head", "tree", "archive_sha256", "acceptance")}))
