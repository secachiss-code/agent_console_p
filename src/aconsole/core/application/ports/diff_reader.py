"""What the diff service needs from git. Every diff command disables external drivers."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, final

DiffChange = Literal["added", "modified", "deleted", "renamed", "binary"]
DiffKind = Literal["context", "add", "del", "meta"]


@dataclass(frozen=True, slots=True, kw_only=True)
class DiffFileStat:
    """One path in `diff/summary`."""

    path: str
    old_path: str | None
    change: DiffChange
    insertions: int
    deletions: int


@dataclass(frozen=True, slots=True, kw_only=True)
class DiffSummary:
    """Worktree compared with `base`. `head` is the worktree, not a commit."""

    base: str
    head: str
    files: tuple[DiffFileStat, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class DiffWordLine:
    """One display line. `word_spans` marks the changed words inside it."""

    kind: DiffKind
    old_no: int | None
    new_no: int | None
    text: str
    word_spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class DiffHunkView:
    """One `@@` hunk. `id` is the header the comment quotes."""

    id: str
    header: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: tuple[DiffWordLine, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class DiffFileText:
    """`diff/file`. `truncated` means `max_lines` stopped the view early."""

    hunks: tuple[DiffHunkView, ...]
    truncated: bool


@final
class DiffReadError(Exception):
    """Git could not produce a diff, or the output is over the byte cap."""

    too_large: bool
    files: int
    byte_count: int

    def __init__(self, message: str, *, too_large: bool, files: int, byte_count: int) -> None:
        super().__init__(message)
        self.too_large = too_large
        self.files = files
        self.byte_count = byte_count


class DiffReader(Protocol):
    """Read a worktree diff. Callers never pass a git argv of their own."""

    def summary(self, repo: Path, base: str | None) -> DiffSummary:
        """Name status and counts of the worktree against `base` (HEAD when omitted)."""
        ...

    def file(self, repo: Path, path: str, base: str | None, max_lines: int) -> DiffFileText:
        """Hunks for one path, stopped after `max_lines` display lines."""
        ...
