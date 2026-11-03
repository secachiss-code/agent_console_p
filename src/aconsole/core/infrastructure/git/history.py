"""Machine-readable history, refs, and worktree state. All of it NUL-separated; nothing here reads prose."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, final

from aconsole.core.application.ports.diff_reader import DiffFileText, DiffSummary
from aconsole.core.application.ports.git_repo import (
    CommitRow,
    DiffSpec,
    GitCommandError,
    MergeProbe,
    RefRow,
    StatusRow,
    WorktreeRow,
)
from aconsole.core.infrastructure.git.diff import GitDiff
from aconsole.core.infrastructure.git.runner import GitRunner
from aconsole.core.infrastructure.git.worktrees import recorded_start

_COMMIT_FIELDS: Final = 5
_REF_ROOTS: Final = ("refs/heads", "refs/tags", "refs/remotes", "refs/aconsole/checkpoints")
_REMOTE_HEAD: Final = "refs/remotes/origin/HEAD"
_BRANCH_PREFIX: Final = "refs/heads/"
_LOCK: Final = ".aconsole/lock"
_NO_PARENT: Final = 1
_CONFLICT: Final = 1
_NOT_ANCESTOR: Final = 1
_MODERN_MERGE: Final = "git merge-tree --write-tree needs git 2.38"
_BAD_RECORD: Final = "git answer was unreadable"
_IN_PROGRESS: Final = (
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("MERGE_HEAD", "merge"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
    ("BISECT_LOG", "bisect"),
)
_STAGED_FIELDS: Final = 8
_RENAME_EXTRA_PATH: Final = "2"


@final
class GitHistory:
    """`GitReader` on one git binary."""

    def __init__(self, runner: GitRunner | None = None, diff: GitDiff | None = None) -> None:
        self._git = GitRunner() if runner is None else runner
        self._diff = GitDiff() if diff is None else diff

    def worktrees(self, root: Path) -> tuple[WorktreeRow, ...]:
        raw = self._git.read(root, "worktree", "list", "--porcelain", "-z").text
        rows: list[WorktreeRow] = []
        for record in raw.split("\0\0"):
            if record.strip("\0"):
                row = _worktree(record, is_main=not rows)
                if row is not None:
                    rows.append(row)
        return tuple(rows)

    def refs(self, root: Path) -> tuple[RefRow, ...]:
        raw = self._git.read(root, "for-each-ref", "--format=%(objectname)%00%(refname)%00", *_REF_ROOTS).text
        pieces = raw.replace("\n", "").split("\0")
        rows = (RefRow(name=pieces[at + 1], oid=pieces[at]) for at in range(0, len(pieces) - 1, 2))
        return tuple(row for row in rows if row.name != _REMOTE_HEAD)

    def remote_head(self, root: Path) -> str | None:
        output = self._git.read(root, "symbolic-ref", "--quiet", _REMOTE_HEAD, codes=frozenset({0, 1, 128}))
        name = output.text.strip()
        if output.code != 0 or not name.startswith("refs/remotes/origin/"):
            return None
        return name.removeprefix("refs/remotes/origin/")

    def status(self, worktree: Path) -> StatusRow:
        raw = self._git.read(worktree, "status", "--porcelain=v2", "-z", "--branch", "--untracked-files=all").text
        return _status(raw, _in_progress(worktree))

    def ahead_behind(self, root: Path, left: str, right: str) -> tuple[int, int]:
        fields = self._git.read(root, "rev-list", "--left-right", "--count", f"{left}...{right}").text.split()
        return int(fields[0]), int(fields[1])

    def merge_base(self, root: Path, left: str, right: str) -> str | None:
        output = self._git.read(root, "merge-base", left, right, codes=frozenset({0, 1}))
        return output.text.strip() or None

    def is_ancestor(self, root: Path, ancestor: str, descendant: str) -> bool:
        output = self._git.read(
            root, "merge-base", "--is-ancestor", ancestor, descendant, codes=frozenset({0, _NOT_ANCESTOR}),
        )
        return output.code == 0

    def merge_probe(self, root: Path, target: str, source: str) -> MergeProbe:
        output = self._git.read(
            root,
            "merge-tree",
            "--write-tree",
            "--no-messages",
            "--name-only",
            "-z",
            target,
            source,
            codes=frozenset({0, _CONFLICT, 2, 128, 129}),
        )
        if output.code not in {0, _CONFLICT}:
            raise GitCommandError(_MODERN_MERGE, detail=output.stderr)
        pieces = [piece for piece in output.text.split("\0") if piece]
        if not pieces:
            raise GitCommandError(_BAD_RECORD)
        return MergeProbe(
            clean=output.code == 0,
            tree=pieces[0] if output.code == 0 else None,
            conflicts=tuple(sorted(set(pieces[1:]))),
        )

    def counts(self, root: Path, left: str, right: str) -> tuple[int, int]:
        raw = self._git.read(
            root, "diff", "--numstat", "-z", "--no-renames", "--no-ext-diff", "--no-textconv", left, right,
        )
        insertions = 0
        deletions = 0
        for line in raw.text.split("\0"):
            fields = line.split("\t", 2)
            if len(fields) == _COMMIT_FIELDS - 2 and fields[0] != "-":
                insertions += int(fields[0])
                deletions += int(fields[1])
        return insertions, deletions

    def start_of(self, root: Path, worktree: Path) -> str | None:
        git_dir = Path(self._git.read(root, "rev-parse", "--absolute-git-dir").text.strip())
        return recorded_start(git_dir, worktree)

    def log(self, root: Path, tips: tuple[str, ...], skip: int, limit: int) -> tuple[CommitRow, ...]:
        if not tips:
            return ()
        raw = self._git.read(
            root,
            "log",
            "--stdin",
            "--topo-order",
            "-z",
            f"--skip={skip}",
            f"--max-count={limit}",
            "--format=%H%x00%P%x00%an%x00%at%x00%s",
            stdin="".join(f"{tip}\n" for tip in tips).encode(),
        ).text
        pieces = raw.split("\0")
        return tuple(
            _commit(pieces[at:at + _COMMIT_FIELDS])
            for at in range(0, len(pieces) - (_COMMIT_FIELDS - 1), _COMMIT_FIELDS)
        )

    def changed_paths(self, root: Path, left: str, right: str) -> tuple[str, ...]:
        raw = self._git.read(root, "diff", "--name-only", "-z", "--no-ext-diff", "--no-textconv", left, right)
        return tuple(path for path in raw.text.split("\0") if path)

    def empty_tree(self, root: Path) -> str:
        return self._git.read(root, "hash-object", "-t", "tree", "--stdin", stdin=b"").text.strip()

    def parent_of(self, root: Path, oid: str) -> str | None:
        parents = self._git.read(root, "rev-list", "--parents", "-n", "1", oid).text.split()
        return parents[_NO_PARENT] if len(parents) > _NO_PARENT else None

    def diff_summary(self, worktree: Path, spec: DiffSpec) -> DiffSummary:
        return self._diff.compare_summary(worktree, spec)

    def diff_file(self, worktree: Path, spec: DiffSpec, path: str, max_lines: int) -> DiffFileText:
        return self._diff.compare_file(worktree, spec, path, max_lines)

    def diff_text(self, worktree: Path, spec: DiffSpec, max_bytes: int) -> str:
        return self._diff.compare_text(worktree, spec, max_bytes)


def _worktree(record: str, *, is_main: bool) -> WorktreeRow | None:
    path = ""
    head: str | None = None
    branch: str | None = None
    detached = False
    for line in record.split("\0"):
        key, _, value = line.partition(" ")
        if key == "worktree":
            path = value
        elif key == "HEAD":
            head = None if set(value) == {"0"} else value
        elif key == "branch":
            branch = value.removeprefix(_BRANCH_PREFIX)
        elif key == "detached":
            detached = True
        elif key == "bare":
            return None
    if not path:
        return None
    return WorktreeRow(path=path, head=head, branch=branch, detached=detached, is_main=is_main)


def _commit(fields: list[str]) -> CommitRow:
    return CommitRow(
        oid=fields[0],
        parents=tuple(fields[1].split()),
        author=fields[2],
        at=datetime.fromtimestamp(int(fields[3]), UTC),
        subject=fields[4],
    )


def _in_progress(worktree: Path) -> str | None:
    """Read the worktree's own git directory. No process: this runs for every worktree on every look."""
    git_dir = _git_dir(worktree)
    if git_dir is None:
        return None
    for name, what in _IN_PROGRESS:
        if (git_dir / name).exists():
            return what
    return None


def _git_dir(worktree: Path) -> Path | None:
    marker = worktree / ".git"
    if marker.is_dir():
        return marker
    try:
        text = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    found = Path(text.removeprefix("gitdir:").strip())
    return found if found.is_absolute() else worktree / found


def _status(raw: str, in_progress: str | None) -> StatusRow:  # noqa: C901 -- one branch per porcelain v2 record type
    staged = unstaged = untracked = conflicted = 0
    index = hashlib.sha256()
    every = hashlib.sha256()
    pieces = raw.split("\0")
    at = 0
    while at < len(pieces):
        piece = pieces[at]
        at += 1
        if not piece:
            continue
        kind = piece[0]
        if kind == "#":
            if piece.startswith("# branch.oid"):
                every.update(piece.encode())
                index.update(piece.encode())
            continue
        if kind == "?":
            if piece[2:] != _LOCK:
                untracked += 1
                every.update(piece.encode())
            continue
        if kind == "u":
            conflicted += 1
            every.update(piece.encode())
            index.update(piece.encode())
            continue
        if kind not in {"1", _RENAME_EXTRA_PATH}:
            continue
        every.update(piece.encode())
        fields = piece.split(" ", _STAGED_FIELDS)
        if fields[1][0] != ".":
            staged += 1
            # X, the HEAD and index modes and hashes, and the rest of the line. The worktree's Y and mode stay out.
            index.update(" ".join((fields[1][0], fields[3], fields[4], *fields[6:])).encode())
        if fields[1][1] != ".":
            unstaged += 1
        if kind == _RENAME_EXTRA_PATH:
            at += 1
    return StatusRow(
        staged=staged,
        unstaged=unstaged,
        untracked=untracked,
        conflicted=conflicted,
        index_digest=index.hexdigest(),
        digest=every.hexdigest(),
        in_progress=in_progress,
    )
