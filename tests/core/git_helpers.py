"""Real repositories for the Git tests."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

GIT = "/usr/bin/git"


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        [GIT, "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@e", *args],
        check=True, capture_output=True, text=True,
    )
    return done.stdout.strip()


def commit(repo: Path, path: str, text: str, message: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = target.write_text(text, encoding="utf-8")
    _ = git(repo, "add", "--", path)
    _ = git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def new_repo(root: Path, branch: str = "trunk") -> Path:
    root.mkdir(parents=True)
    _ = git(root, "init", "-q", "-b", branch)
    return root


def add_worktree(repo: Path, dest: Path, branch: str) -> Path:
    _ = git(repo, "worktree", "add", "-q", "-b", branch, str(dest))
    return dest
