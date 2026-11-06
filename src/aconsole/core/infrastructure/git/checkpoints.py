"""Checkpoint commits on side refs. HEAD, the index, and ignored files stay put.

The snapshot uses a private index, so `git add` does not touch the user's index.
Restore writes that tree into the worktree and removes other non-ignored files.
This module does not run `git stash` or `git reset --hard`.
"""

from __future__ import annotations

import errno
import os
import tempfile
from pathlib import Path
from typing import Final, final

from aconsole.core.application.ports.checkpoint_store import CheckpointStoreError, TreeCapture
from aconsole.core.application.ports.git_repo import GitCommandError
from aconsole.core.infrastructure.git.runner import GitCall, GitRunner
from aconsole.core.infrastructure.host_bins import host_binary

_TIMEOUT_S: Final = 30.0
_OK: Final = 0
_GIT: Final = host_binary("git")
_FAILED: Final = "git command failed"
_TIMED_OUT: Final = "git timed out"
_MISSING: Final = "git binary missing"
_ABS_GIT: Final = "git binary path must be absolute"
_ABS_REPO: Final = "repository path must be absolute"
_BAD_NUMSTAT: Final = "git numstat line was unreadable"
# A checkpoint is the core's own ref, not the user's commit: it needs no `user.name`, and a machine without one
# would otherwise get no checkpoints at all.
_AUTHOR: Final = {
    "GIT_AUTHOR_NAME": "AConsole",
    "GIT_AUTHOR_EMAIL": "aconsole@localhost",
    "GIT_COMMITTER_NAME": "AConsole",
    "GIT_COMMITTER_EMAIL": "aconsole@localhost",
}


@final
class CheckpointGitError(CheckpointStoreError):
    """Git could not capture or restore a checkpoint."""

    detail: str

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


@final
class GitCheckpoints:
    """Side refs under `refs/aconsole/checkpoints/<tab>/<n>`."""

    _runner: GitRunner
    _timeout_s: float

    def __init__(self, git: Path = _GIT, *, timeout_s: float = _TIMEOUT_S) -> None:
        if timeout_s <= 0:
            message = "timeout must be positive"
            raise CheckpointGitError(message)
        try:
            self._runner = GitRunner(git, timeout_s=timeout_s)
        except GitCommandError as exc:
            raise CheckpointGitError(_ABS_GIT) from exc
        self._timeout_s = timeout_s

    def capture(self, repo: Path, ref: str, message: str) -> TreeCapture:
        _absolute_repo(repo)
        found = self._runner.read(repo, "rev-parse", "--verify", "--quiet", "HEAD", codes=frozenset({0, 1}))
        parent = found.text.strip()
        base = parent or self._stdout(repo, ("hash-object", "-w", "-t", "tree", "--stdin")).strip()
        with tempfile.NamedTemporaryFile(prefix="aconsole-index-") as index:
            _ = self._stdout(repo, ("read-tree", base), index=index.name)
            _ = self._stdout(repo, ("add", "-A", "--", ".", ":(exclude).aconsole/lock"), index=index.name)
            tree = self._stdout(repo, ("write-tree",), index=index.name).strip()
        parents = ("-p", parent) if parent else ()
        commit = self._stdout(repo, ("commit-tree", tree, *parents, "-m", message), author=True).strip()
        _ = self._stdout(repo, ("update-ref", ref, commit))
        return _capture(self._numstat(repo, base, commit), self._paths(repo, commit), tree)

    def materialize(self, repo: Path, ref: str) -> None:
        _absolute_repo(repo)
        snapshot = set(self._paths(repo, ref))
        for path in sorted(snapshot):
            _ = self._stdout(repo, ("restore", "--source", ref, "--worktree", "--", path))
        current = set(self._z(repo, ("ls-files", "-c", "-o", "--exclude-standard", "-z")))
        # A tracked child must be checked before an untracked symlink replacing its parent is removed.
        for path in sorted(current - snapshot, reverse=True):
            if path == ".aconsole/lock":
                continue
            try:
                remove_worktree_file(repo, path)
            except OSError as exc:
                raise CheckpointGitError(_FAILED, detail=str(exc)) from exc

    def delete(self, repo: Path, ref: str) -> None:
        _absolute_repo(repo)
        _ = self._stdout(repo, ("update-ref", "-d", ref))

    def paths(self, repo: Path, ref: str) -> tuple[str, ...]:
        _absolute_repo(repo)
        return self._paths(repo, ref)

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

    def _stdout(self, repo: Path, args: tuple[str, ...], *, index: str | None = None, author: bool = False) -> str:
        extra: dict[str, str] = {}
        if author:
            extra.update(_AUTHOR)
        if index is not None:
            extra["GIT_INDEX_FILE"] = index
        try:
            completed = self._runner.invoke(
                repo,
                args,
                GitCall(env=extra, timeout_s=self._timeout_s, write=True),
            )
        except GitCommandError as exc:
            if str(exc) == _TIMED_OUT:
                raise CheckpointGitError(_TIMED_OUT) from exc
            if str(exc) == _MISSING:
                raise CheckpointGitError(_MISSING) from exc
            raise CheckpointGitError(_FAILED, detail=exc.detail) from exc
        if completed.code != _OK:
            raise CheckpointGitError(_FAILED, detail=completed.stderr)
        return completed.text


_MIN_NUMSTAT: Final = 3


def _absolute_repo(repo: Path) -> None:
    if not repo.is_absolute():
        raise CheckpointGitError(_ABS_REPO)


def _count(field: str) -> int:
    if field == "-":
        return 0
    return int(field)


def _capture(rows: tuple[tuple[int, int], ...], files: tuple[str, ...], tree: str) -> TreeCapture:
    insertions = sum(row[0] for row in rows)
    deletions = sum(row[1] for row in rows)
    return TreeCapture(
        files_changed=len(rows),
        insertions=insertions,
        deletions=deletions,
        files=files,
        tree=tree,
    )


def remove_worktree_file(repo: Path, relative: str) -> None:
    """Unlink through opened directories; neither parent nor leaf symlinks are followed.

    Holding directory descriptors also keeps a concurrent rename or symlink substitution
    from redirecting the unlink to another directory. A missing path is already removed.
    """
    path = Path(relative)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise OSError(errno.EPERM, "restore path must stay inside the worktree", relative)
    directory = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        os.unlink(path.parts[-1], dir_fd=directory)
    except FileNotFoundError:
        return
    finally:
        os.close(directory)
