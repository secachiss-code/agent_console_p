"""What the checkpoint service needs from git. The commands live in infrastructure."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True, kw_only=True)
class TreeCapture:
    """One side commit: the non-ignored worktree, not the user's HEAD."""

    files_changed: int
    insertions: int
    deletions: int
    files: tuple[str, ...]


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
