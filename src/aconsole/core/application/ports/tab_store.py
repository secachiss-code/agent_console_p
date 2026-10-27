"""Port for the tab table. Tokens and secrets never cross it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredTab:
    """One open tab and the workspace it points at. No ingest or sidecar secret."""

    id: str
    project_id: str
    project_name: str
    project_root: str
    container: str
    agent: str
    title: str
    order: int
    status: str
    status_detail: str | None
    since: datetime
    created_at: datetime
    worktree: str
    branch: str | None
    base_branch: str | None
    dirty: bool
    workspace_mode: str
    plan_node: str | None
    session: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class TabDirectory:
    """`Settings.worktrees_dir` and the settings row revision. Both are absent when unset."""

    worktrees_dir: str | None
    rev: int


class TabStore(Protocol):
    """Writes go through the single persistence writer. Reads of open tabs do too."""

    def save(self, tab: StoredTab) -> None:
        """Insert or update the project, the workspace, and the tab."""
        ...

    def remove(self, tab_id: str, *, closed_at: datetime, drop_worktree: bool) -> None:
        """Mark the tab closed. `drop_worktree` records that the directory was removed."""
        ...

    def load_open(self) -> tuple[StoredTab, ...]:
        """Tabs with no `closed_at`, in order."""
        ...

    def settings(self) -> TabDirectory:
        """The stored worktree directory, or none when settings were never written."""
        ...
