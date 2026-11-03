"""What the Git services need from git. No caller passes an argv of its own."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, final

from aconsole.core.application.ports.diff_reader import DiffFileText, DiffSummary
from aconsole.core.domain.git_operation import GitOperationRecord


@final
class GitCommandError(Exception):
    """Git failed or timed out. `detail` is what it said, for the log."""

    detail: str

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


@dataclass(frozen=True, slots=True, kw_only=True)
class WorktreeRow:
    path: str
    head: str | None
    branch: str | None
    detached: bool
    is_main: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class RefRow:
    name: str
    oid: str


@dataclass(frozen=True, slots=True, kw_only=True)
class StatusRow:
    """`index_digest` changes only when the index or HEAD does; `digest` with any listed file."""

    staged: int
    unstaged: int
    untracked: int
    conflicted: int
    index_digest: str
    digest: str
    in_progress: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class CommitRow:
    oid: str
    parents: tuple[str, ...]
    author: str
    at: datetime
    subject: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MergeProbe:
    clean: bool
    tree: str | None
    conflicts: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class DiffSpec:
    """Two sides of a comparison. `right` None is the worktree's files. `cached` compares the index with `left`."""

    left: str | None
    right: str | None = None
    cached: bool = False
    untracked: bool = False


class GitReader(Protocol):
    def worktrees(self, root: Path) -> tuple[WorktreeRow, ...]:
        """Every worktree git knows, the main checkout first."""
        ...

    def refs(self, root: Path) -> tuple[RefRow, ...]:
        """Branches, tags, remote branches, and checkpoint refs."""
        ...

    def remote_head(self, root: Path) -> str | None:
        """The branch `origin/HEAD` names, without the remote's name."""
        ...

    def status(self, worktree: Path) -> StatusRow: ...

    def ahead_behind(self, root: Path, left: str, right: str) -> tuple[int, int]:
        """Commits only on `left`, and only on `right`."""
        ...

    def merge_base(self, root: Path, left: str, right: str) -> str | None: ...

    def is_ancestor(self, root: Path, ancestor: str, descendant: str) -> bool: ...

    def merge_probe(self, root: Path, target: str, source: str) -> MergeProbe:
        """`git merge-tree`: neither the index nor any file moves, but objects may be written."""
        ...

    def counts(self, root: Path, left: str, right: str) -> tuple[int, int]:
        """Lines added and removed from `left` to `right`."""
        ...

    def start_of(self, root: Path, worktree: Path) -> str | None:
        """The commit this core recorded when it created the worktree."""
        ...

    def log(self, root: Path, tips: tuple[str, ...], skip: int, limit: int) -> tuple[CommitRow, ...]:
        """Commits reachable from `tips`, children before parents."""
        ...

    def changed_paths(self, root: Path, left: str, right: str) -> tuple[str, ...]: ...

    def empty_tree(self, root: Path) -> str: ...

    def parent_of(self, root: Path, oid: str) -> str | None:
        """The first parent, or None for a root commit."""
        ...

    def diff_summary(self, worktree: Path, spec: DiffSpec) -> DiffSummary: ...

    def diff_file(self, worktree: Path, spec: DiffSpec, path: str, max_lines: int) -> DiffFileText: ...

    def diff_text(self, worktree: Path, spec: DiffSpec, max_bytes: int) -> str:
        """The plain patch. Files nobody added are not in it."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class GitDone:
    """Git ran. `ok` false means it refused, and `said` is why: a hook, signing, or a conflict."""

    ok: bool
    said: str
    oid: str | None = None


class GitWriter(Protocol):
    """Changes to one worktree. The caller has checked the worktree and holds its lock."""

    def stage(self, worktree: Path, paths: tuple[str, ...]) -> GitDone: ...

    def unstage(self, worktree: Path, paths: tuple[str, ...], *, has_head: bool) -> GitDone: ...

    def commit(self, worktree: Path, message: str) -> GitDone:
        """Commit the index, hooks and signing included."""
        ...

    def head(self, worktree: Path) -> str | None: ...

    def in_source(self, worktree: Path, oid: str, path: str) -> bool: ...

    def file_counts(self, worktree: Path, oid: str, path: str) -> tuple[int, int]:
        """Lines a restore would add and remove."""
        ...

    def restore_file(self, worktree: Path, oid: str, path: str) -> GitDone:
        """Make this one file match `oid`. A file `oid` does not hold is removed. The index stays."""
        ...

    def safety_ref(self, worktree: Path, ref: str, label: str) -> None:
        """Keep the worktree's files on a side ref before something replaces them."""
        ...

    def merge(self, worktree: Path, oid: str, mode: str, message: str) -> GitDone:
        """Merge into the branch checked out here. A refusal leaves the worktree as it was."""
        ...

    def tree_of(self, root: Path, oid: str) -> str | None: ...

    def commit_tree(self, root: Path, tree: str, parents: tuple[str, ...], message: str) -> GitDone:
        """A new commit no branch points at yet. `oid` is its id."""
        ...

    def move_ref(self, root: Path, ref: str, new: str, old: str) -> GitDone:
        """Only if `ref` is still at `old`."""
        ...


class GitOperationJournal(Protocol):
    def save(self, record: GitOperationRecord) -> bool:
        """False when the row was not written. The caller keeps the record and writes it again."""
        ...

    def find(self, operation_id: str) -> GitOperationRecord | None: ...

    def unfinished(self) -> tuple[GitOperationRecord, ...]: ...
