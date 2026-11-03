"""What the Git services need from git. No caller passes an argv of its own."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, final

from aconsole.core.application.ports.diff_reader import DiffFileText, DiffSummary


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
