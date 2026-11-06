"""What the checkpoint service needs from git. The commands live in infrastructure."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from aconsole.core.domain.checkpoints import Checkpoint


class CheckpointStoreError(Exception):
    """A snapshot could not be captured or materialized."""


@dataclass(frozen=True, slots=True, kw_only=True)
class TreeCapture:
    """One side commit: the non-ignored worktree, not the user's HEAD."""

    files_changed: int
    insertions: int
    deletions: int
    files: tuple[str, ...]
    tree: str = ""


class CheckpointStore(Protocol):
    """Capture a worktree, put it back, and drop a ref. No `git stash` or `git reset --hard`."""

    def capture(self, repo: Path, ref: str, message: str) -> TreeCapture:
        """Point `ref` at a new commit of the non-ignored worktree. Leave HEAD and the index."""
        ...

    def materialize(self, repo: Path, ref: str) -> None:
        """Make non-ignored worktree files match `ref`. Ignored files stay as they are."""
        ...

    def delete(self, repo: Path, ref: str) -> None:
        """Remove `ref`. Missing refs are already gone."""
        ...

    def paths(self, repo: Path, ref: str) -> tuple[str, ...]:
        """The files `ref` holds. The journal keeps only their count."""
        ...


class CheckpointJournal(Protocol):
    """Checkpoint rows, so the list outlives the core. The refs stay in git."""

    def load(self, tab_id: str) -> tuple[Checkpoint, ...]:
        """The tab's checkpoints, oldest first. Their `files` stay empty."""
        ...

    def save(self, checkpoint: Checkpoint, tree: str) -> None:
        """Add one row."""
        ...

    def delete(self, tab_id: str, numbers: tuple[int, ...]) -> None:
        """Remove the rows retention dropped."""
        ...
