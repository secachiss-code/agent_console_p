"""Checkpoint commits on side refs. HEAD, the index, and ignored files stay put.

The snapshot uses a private index, so `git add` does not touch the user's index.
Restore writes that tree into the worktree and removes other non-ignored files.
This module does not run `git stash` or `git reset --hard`.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Final, final

from aconsole.core.application.ports.checkpoint_store import TreeCapture

_TIMEOUT_S: Final = 30.0
_OK: Final = 0
_GIT: Final = Path("/usr/bin/git")
_FAILED: Final = "git command failed"
_TIMED_OUT: Final = "git timed out"
_MISSING: Final = "git binary missing"
_ABS_GIT: Final = "git binary path must be absolute"
_ABS_REPO: Final = "repository path must be absolute"
_NO_HEAD: Final = "repository has no commits"
_BAD_NUMSTAT: Final = "git numstat line was unreadable"


@final
class CheckpointGitError(Exception):
    """Git could not capture or restore a checkpoint."""

    detail: str

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


@final
class GitCheckpoints:
    """Side refs under `refs/aconsole/checkpoints/<tab>/<n>`."""

    _git: Path
    _timeout_s: float

    def __init__(self, git: Path = _GIT, *, timeout_s: float = _TIMEOUT_S) -> None:
        if not git.is_absolute():
            raise CheckpointGitError(_ABS_GIT)
        if timeout_s <= 0:
            message = "timeout must be positive"
            raise CheckpointGitError(message)
        self._git = git
        self._timeout_s = timeout_s

    def capture(self, repo: Path, ref: str, message: str) -> TreeCapture:
        _absolute_repo(repo)
        parent = self._stdout(repo, ("rev-parse", "--verify", "HEAD")).strip()
        if not parent:
            raise CheckpointGitError(_NO_HEAD)
        with tempfile.NamedTemporaryFile(prefix="aconsole-index-") as index:
            _ = self._stdout(repo, ("read-tree", "HEAD"), index=index.name)
            _ = self._stdout(repo, ("add", "-A", "--", "."), index=index.name)
            tree = self._stdout(repo, ("write-tree",), index=index.name).strip()
        commit = self._stdout(repo, ("commit-tree", tree, "-p", parent, "-m", message)).strip()
        _ = self._stdout(repo, ("update-ref", ref, commit))
        return _capture(self._numstat(repo, parent, commit), self._paths(repo, commit))

    def materialize(self, repo: Path, ref: str) -> None:
        _absolute_repo(repo)
        snapshot = set(self._paths(repo, ref))
        for path in sorted(snapshot):
            _ = self._stdout(repo, ("restore", "--source", ref, "--worktree", "--", path))
        current = set(self._z(repo, ("ls-files", "-c", "-o", "--exclude-standard", "-z")))
        for path in sorted(current - snapshot):
            file = repo / path
            if file.is_file():
                file.unlink()

    def delete(self, repo: Path, ref: str) -> None:
        _absolute_repo(repo)
        _ = self._stdout(repo, ("update-ref", "-d", ref))

    def _paths(self, repo: Path, ref: str) -> tuple[str, ...]:
        return self._z(repo, ("ls-tree", "-r", "-z", "--name-only", ref))

    def _numstat(self, repo: Path, parent: str, commit: str) -> tuple[tuple[int, int], ...]:
        text = self._stdout(
            repo,
            ("diff", "--numstat", "--no-ext-diff", "--no-textconv", parent, commit),
        )
        rows: list[tuple[int, int]] = []
        for line in text.splitlines():
            parts = line.split("\t")
            if len(parts) < _MIN_NUMSTAT:
                message = _BAD_NUMSTAT
                raise CheckpointGitError(message, detail=line)
            rows.append((_count(parts[0]), _count(parts[1])))
        return tuple(rows)

    def _z(self, repo: Path, args: tuple[str, ...]) -> tuple[str, ...]:
        raw = self._stdout(repo, args)
        if raw == "":
            return ()
        return tuple(piece for piece in raw.split("\0") if piece)

    def _stdout(self, repo: Path, args: tuple[str, ...], *, index: str | None = None) -> str:
        env = os.environ.copy()
        if index is not None:
            env["GIT_INDEX_FILE"] = index
        try:
            completed = subprocess.run(  # noqa: S603 -- argv is a list; the git path is absolute
                (str(self._git), "-C", str(repo), *args),
                check=False,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise CheckpointGitError(_TIMED_OUT) from exc
        except FileNotFoundError as exc:
            raise CheckpointGitError(_MISSING) from exc
        if completed.returncode != _OK:
            raise CheckpointGitError(_FAILED, detail=completed.stderr)
        return completed.stdout


_MIN_NUMSTAT: Final = 3


def _absolute_repo(repo: Path) -> None:
    if not repo.is_absolute():
        raise CheckpointGitError(_ABS_REPO)


def _count(field: str) -> int:
    if field == "-":
        return 0
    return int(field)


def _capture(rows: tuple[tuple[int, int], ...], files: tuple[str, ...]) -> TreeCapture:
    insertions = sum(row[0] for row in rows)
    deletions = sum(row[1] for row in rows)
    return TreeCapture(
        files_changed=len(rows),
        insertions=insertions,
        deletions=deletions,
        files=files,
    )
