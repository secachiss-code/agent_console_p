"""Finite real Git controls; all lock files belong to this disposable fixture."""

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parent
records = []


def git(repo, *args, ok=True):
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@e", *args],
        capture_output=True,
        text=True,
        timeout=15,
    )
    records.append(
        {"argv": list(args), "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    )
    if ok:
        assert result.returncode == 0, records[-1]
    return result


for algorithm in ("sha1", "sha256"):
    with tempfile.TemporaryDirectory(prefix="aconsole-qa27-") as directory:
        repo = Path(directory) / "repo"
        tree = Path(directory) / "wt"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "develop", "--object-format=" + algorithm)
        file = repo / "a.txt"
        file.write_text("first\n")
        git(repo, "add", "a.txt")
        git(repo, "commit", "-qm", "first")
        index = repo / ".git/index"
        stamp = file.stat()
        os.utime(file, ns=(stamp.st_atime_ns, stamp.st_mtime_ns - 2_000_000_000))
        original = index.read_bytes()
        assert git(repo, "--no-optional-locks", "status", "--porcelain").stdout == ""
        assert index.read_bytes() == original
        assert git(repo, "status", "--porcelain").stdout == ""
        assert index.read_bytes() != original, "ordinary status should refresh stale index stat data"
        git(repo, "worktree", "add", "-q", "-b", "ac/feature", str(tree))
        (tree / "a.txt").write_text("work\n")
        git(tree, "commit", "-qam", "work side")
        before = git(tree, "rev-parse", "HEAD").stdout
        file.write_text("base\n")
        git(repo, "commit", "-qam", "base side")
        conflict = git(tree, "rebase", "develop", ok=False)
        assert conflict.returncode != 0
        assert "UU a.txt" in git(tree, "--no-optional-locks", "status", "--porcelain").stdout
        gitdir = Path(git(tree, "rev-parse", "--absolute-git-dir").stdout.strip())
        index = gitdir / "index"
        lock = gitdir / "index.lock"
        conflict_index = index.read_bytes()
        conflict_head = git(tree, "rev-parse", "HEAD").stdout
        # Exclusive creation proves this control owns the lock it later releases.
        with lock.open("xb") as owned:
            owned.write(b"QA27 fixture lock\n")
        try:
            assert "UU a.txt" in git(tree, "--no-optional-locks", "status", "--porcelain").stdout
            denied = git(tree, "rebase", "--abort", ok=False)
            assert denied.returncode != 0 and "index.lock" in denied.stderr
            assert lock.read_bytes() == b"QA27 fixture lock\n"
            assert index.read_bytes() == conflict_index
            assert git(tree, "rev-parse", "HEAD").stdout == conflict_head
            assert (tree / "a.txt").read_text().startswith("<<<<<<<")
        finally:
            lock.unlink()
        git(tree, "rebase", "--abort")
        assert git(tree, "--no-optional-locks", "status", "--porcelain").stdout == ""
        assert git(tree, "rev-parse", "HEAD").stdout == before
        assert (tree / "a.txt").read_text() == "work\n"
        assert not (gitdir / "rebase-merge").exists()
        assert not (gitdir / "rebase-apply").exists()
        records.append(
            {
                "algorithm": algorithm,
                "passed": True,
                "restored_head": before.strip(),
                "conflict_index_sha256": hashlib.sha256(conflict_index).hexdigest(),
            }
        )

(OUT / "controls.json").write_text(json.dumps(records, indent=2) + "\n")
print("SHA1/SHA256: status refresh, read-only index preservation, busy-lock refusal and release-success passed")
