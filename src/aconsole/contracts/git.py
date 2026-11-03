"""`git/*`: one project's worktrees, its commit graph, and what may be done to them.

The words these contracts keep apart:

- `base_branch` is where a project's work lands. It is the project's `default_branch`, never assumed to be `main`.
- `start_oid` is the commit a tab's branch was created from. The base moves on; this does not.
- `behind`, `dirty`, and a merge conflict are independent. A branch can be behind its base and still merge cleanly.
- A merge probe judges two commits. It says nothing about a worktree's uncommitted files, so
  `MergeProbeView.status` is about commits only and `WorktreeGitState.dirty` is a separate fact.
- A checkpoint's Git parent is the HEAD it was taken on, so snapshots of one tab are usually siblings. Their order
  in time is `GitNodeView.previous_snapshot`, which is not a Git edge.

A worktree with no open tab is a closed tab's kept worktree. A branch with no worktree is a kept branch.
`head_oid` is None and `empty` is true in a repository with no commits; `branch` is None on a detached HEAD.
"""

from datetime import datetime
from typing import Annotated, Literal

import msgspec
from msgspec import Meta

from aconsole.contracts.common import AgentKind, NodeId, PlanId, ProjectId, TabId, TabStatus

GitNodeKind = Literal["commit", "checkpoint"]
MergeStatus = Literal["clean", "conflict", "unknown", "not_applicable"]
InProgress = Literal["merge", "rebase", "cherry-pick", "revert", "bisect"]
MergeMode = Literal["fast_forward", "merge", "squash"]
DiffSourceKind = Literal["base", "commit", "index", "worktree", "between"]
OperationKind = Literal["merge", "rebase", "cherry-pick"]
OperationStatus = Literal["started", "conflicted", "merged", "finished", "failed", "aborted"]
Limit = Annotated[int, Meta(ge=1, le=1000)]


class MergeProbeView(msgspec.Struct, frozen=True, kw_only=True):
    """`git merge-tree` of exactly these two commits. Files nobody committed are not in it."""

    status: MergeStatus
    source_oid: str | None
    target_oid: str | None
    conflicts: tuple[str, ...] = ()
    insertions: int = 0
    deletions: int = 0


class WorktreeGitState(msgspec.Struct, frozen=True, kw_only=True):
    path: str
    is_main: bool
    branch: str | None
    head_oid: str | None
    detached: bool
    tab_id: TabId | None
    tab_title: str | None
    agent: AgentKind | None
    tab_status: TabStatus | None
    busy: bool
    plan_id: PlanId | None
    plan_node: NodeId | None
    start_oid: str | None
    ahead: int
    behind: int
    staged: int
    unstaged: int
    untracked: int
    conflicted: int
    dirty: bool
    index_digest: str
    in_progress: InProgress | None
    merge: MergeProbeView


class BranchView(msgspec.Struct, frozen=True, kw_only=True):
    name: str
    oid: str
    worktree: str | None
    ahead: int
    behind: int


class GitOperationView(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    kind: OperationKind
    status: OperationStatus
    mode: MergeMode | None
    source: str
    target: str
    source_oid: str
    target_oid: str
    result_oid: str | None
    tab_id: TabId | None
    plan_node: NodeId | None
    message: str | None
    follow_up_pending: bool
    created_at: datetime
    updated_at: datetime


class GitStateParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId


class GitStateResult(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    revision: str
    root: str
    empty: bool
    base_branch: str | None
    base_oid: str | None
    worktrees: tuple[WorktreeGitState, ...]
    branches: tuple[BranchView, ...]
    operations: tuple[GitOperationView, ...]


class GitWatchParams(msgspec.Struct, frozen=True, kw_only=True):
    """A client is showing this project. Good for 30 seconds, and it runs no git: send it again to keep the updates."""

    project_id: ProjectId


class GitGraphParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    tab_id: TabId | None = None
    limit: Limit = 200
    cursor: str | None = None


class GitRefView(msgspec.Struct, frozen=True, kw_only=True):
    name: str
    kind: Literal["base", "branch", "tag", "remote", "checkpoint", "head"]
    tab_id: TabId | None = None


class GitNodeView(msgspec.Struct, frozen=True, kw_only=True):
    oid: str
    kind: GitNodeKind
    parents: tuple[str, ...]
    refs: tuple[GitRefView, ...]
    subject: str
    author: str
    at: datetime
    tab_id: TabId | None
    plan_node: NodeId | None
    checkpoint_id: str | None = None
    turn: int | None = None
    previous_snapshot: str | None = None


class GitGraphResult(msgspec.Struct, frozen=True, kw_only=True):
    """One page, newest first. `next_cursor` is None on the last page. A parent may lie on a later page."""

    version: str
    nodes: tuple[GitNodeView, ...]
    next_cursor: str | None


class DiffSource(msgspec.Struct, frozen=True, kw_only=True):
    """What to compare. `base` and `worktree` read a tab's or checkout's files, the others only commits."""

    kind: DiffSourceKind
    worktree: str | None = None
    oid: str | None = None
    against: str | None = None


class GitDiffParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    source: DiffSource


class GitDiffFileParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    source: DiffSource
    path: str
    max_lines: int = 2000


class GitPathsParams(msgspec.Struct, frozen=True, kw_only=True):
    """Stage or unstage whole files. `expect_*` are what the user was shown."""

    project_id: ProjectId
    worktree: str
    paths: tuple[str, ...]
    expect_head: str | None
    expect_index: str


class GitCommitParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    worktree: str
    message: Annotated[str, Meta(min_length=1)]
    expect_head: str | None
    expect_index: str


class GitChangeResult(msgspec.Struct, frozen=True, kw_only=True):
    """`ok` false is a refusal from Git itself: a hook or commit signing. `detail` is what Git said."""

    ok: bool
    head_oid: str | None
    detail: str | None


class RestoreFilePreviewParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    worktree: str
    path: str
    source_oid: str


class RestoreFilePreviewResult(msgspec.Struct, frozen=True, kw_only=True):
    path: str
    source_oid: str
    insertions: int
    deletions: int
    exists_in_source: bool
    confirm_token: str


class RestoreFileParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    worktree: str
    path: str
    source_oid: str
    confirm_token: str


class MergePreviewParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    source: str
    target: str | None = None


class MergePreviewResult(msgspec.Struct, frozen=True, kw_only=True):
    """Valid for exactly `source_oid` into `target_oid`. `modes` is what can be done with them now."""

    source: str
    target: str
    source_oid: str
    target_oid: str
    probe: MergeProbeView
    modes: tuple[MergeMode, ...]
    source_dirty: bool
    target_worktree: str | None
    blockers: tuple[str, ...]
    confirm_token: str


class MergeWorktreeParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    source: str
    target: str
    source_oid: str
    target_oid: str
    mode: MergeMode
    confirm_token: str
    message: str | None = None


class GitOperationResult(msgspec.Struct, frozen=True, kw_only=True):
    operation: GitOperationView


class GitOperationParams(msgspec.Struct, frozen=True, kw_only=True):
    operation_id: str


class CleanupWorktreeParams(msgspec.Struct, frozen=True, kw_only=True):
    """Removing a worktree also deletes its ignored files, so that takes `discard_ignored`."""

    project_id: ProjectId
    worktree: str
    discard_ignored: bool = False


class CleanupWorktreeResult(msgspec.Struct, frozen=True, kw_only=True):
    removed: bool
    branch_deleted: bool
    kept_ref: str | None


class FactReviewView(msgspec.Struct, frozen=True, kw_only=True):
    """A fact whose code moved since it was verified. It needs a look; it is not thereby wrong."""

    id: str
    title: str
    verified_commit: str
    changed_paths: tuple[str, ...]


class FactsToReviewResult(msgspec.Struct, frozen=True, kw_only=True):
    head_oid: str | None
    facts: tuple[FactReviewView, ...]


class RebaseParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    worktree: str
    onto_oid: str
    expect_head: str


class CherryPickParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    worktree: str
    oid: str
    expect_head: str


class WorktreeOperationParams(msgspec.Struct, frozen=True, kw_only=True):
    """Continue or abort what is in progress in this worktree."""

    project_id: ProjectId
    worktree: str


class SendDiffPreviewParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    source: DiffSource
    to_tab: TabId


class SendDiffPreviewResult(msgspec.Struct, frozen=True, kw_only=True):
    """Who gets the text and how much of it. The token is good for this text only."""

    to_tab: TabId
    to_title: str
    files: int
    byte_count: int
    confirm_token: str


class SendDiffParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    source: DiffSource
    to_tab: TabId
    confirm_token: str
