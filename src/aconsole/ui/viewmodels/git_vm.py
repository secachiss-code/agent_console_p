"""Calls and row data of the Git page. No GTK here."""

from dataclasses import dataclass
from typing import Final, Literal

from aconsole.contracts.app import CommandResult, DiffFileResult, DiffSummaryResult, EmptyResult
from aconsole.contracts.common import ProjectId, TabId
from aconsole.contracts.git import (
    CherryPickParams,
    CleanupWorktreeParams,
    CleanupWorktreeResult,
    DiffSource,
    FactsToReviewResult,
    GitChangeResult,
    GitCommitParams,
    GitDiffFileParams,
    GitDiffParams,
    GitGraphParams,
    GitGraphResult,
    GitNodeView,
    GitOperationResult,
    GitOperationView,
    GitPathsParams,
    GitStateParams,
    GitStateResult,
    GitWatchParams,
    MergeMode,
    MergePreviewParams,
    MergePreviewResult,
    MergeWorktreeParams,
    RebaseParams,
    RestoreFileParams,
    RestoreFilePreviewParams,
    RestoreFilePreviewResult,
    SendDiffParams,
    SendDiffPreviewParams,
    SendDiffPreviewResult,
    WorktreeGitState,
    WorktreeOperationParams,
)
from aconsole.contracts.work_sessions import WorkSessionParams, WorkSessionView
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.viewmodels.logic.git_lanes import BASE_COLOR, LaneLayout, RowLayout, tab_color

_PAGE: Final = 200
_SHORT: Final = 8
UNCOMMITTED_PREFIX: Final = "worktree:"
BadgeKind = Literal["ok", "warn", "bad", "info"]
WorktreeMode = Literal["worktree", "index", "base"]


@dataclass(frozen=True, slots=True)
class Badge:
    """`key` names the message; `values` fill it. `behind` and `conflict` never replace each other."""

    key: str
    kind: BadgeKind
    values: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class GraphRow:
    """One row of the list: a commit, a snapshot, or a worktree's uncommitted files."""

    key: str
    layout: RowLayout
    node: GitNodeView | None
    worktree: WorktreeGitState | None


def short(oid: str | None) -> str:
    return "" if oid is None else oid[:_SHORT]


def merge_badges(row: WorktreeGitState) -> tuple[Badge, ...]:
    """What the branch's commits can do. Uncommitted files get their own badge: the probe did not see them."""
    badges: list[Badge] = []
    probe = row.merge
    if probe.status == "conflict":
        badges.append(Badge("git.badge_conflict", "bad", (("count", len(probe.conflicts)),)))
    elif probe.status == "clean":
        badges.append(
            Badge(
                "git.badge_ready",
                "ok",
                (("insertions", probe.insertions), ("deletions", probe.deletions)),
            )
        )
    if row.behind > 0:
        badges.append(Badge("git.badge_behind", "warn", (("count", row.behind),)))
    if row.dirty:
        badges.append(Badge("git.badge_unchecked", "info"))
    if row.in_progress is not None:
        badges.append(Badge("git.badge_in_progress", "warn"))
    if row.busy:
        badges.append(Badge("git.badge_busy", "info"))
    return tuple(badges)


def uncommitted_rows(state: GitStateResult, tab: TabId | None) -> tuple[WorktreeGitState, ...]:
    return tuple(
        row for row in state.worktrees if row.dirty and row.head_oid is not None
        and (tab is None or row.tab_id == tab or tab in row.tab_ids)
    )


def node_source(node: GitNodeView) -> DiffSource:
    """A snapshot shows that step, so against the previous snapshot when there is one."""
    if node.previous_snapshot is not None:
        return DiffSource(kind="between", oid=node.oid, against=node.previous_snapshot)
    return DiffSource(kind="commit", oid=node.oid)


def worktree_source(row: WorktreeGitState, mode: WorktreeMode) -> DiffSource:
    return DiffSource(kind=mode, worktree=row.path)


class GraphBuilder:
    """Rows of one graph version. Pages keep extending the same lanes."""

    def __init__(self, base_oid: str | None) -> None:
        self._lanes: LaneLayout = LaneLayout(base_oid=base_oid)

    def uncommitted(self, row: WorktreeGitState) -> GraphRow:
        parent = row.head_oid or ""
        color = BASE_COLOR if row.tab_id is None else tab_color(str(row.tab_id))
        layout = self._lanes.add(f"{UNCOMMITTED_PREFIX}{row.path}", (parent,), color=color)
        return GraphRow(key=f"{UNCOMMITTED_PREFIX}{row.path}", layout=layout, node=None, worktree=row)

    def commit(self, node: GitNodeView) -> GraphRow:
        color = None if node.tab_id is None else tab_color(str(node.tab_id))
        layout = self._lanes.add(node.oid, node.parents, color=color, time_link=node.previous_snapshot)
        return GraphRow(key=node.oid, layout=layout, node=node, worktree=None)


_KIB: Final = 1024


class GitViewModel:
    def __init__(self, client: CoreClient, project_id: ProjectId) -> None:
        self.client: CoreClient = client
        self.project_id: ProjectId = project_id
        self.work_session_id: str | None = None
        self.actor_tab_id: TabId | None = None

    async def state(self) -> GitStateResult:
        return await self.client.request("git/state", GitStateParams(
            project_id=self.project_id, work_session_id=self.work_session_id,
        ), GitStateResult)

    async def watch(self) -> None:
        """Keep the core's updates for this project coming. No git runs for this."""
        _ = await self.client.request("git/watch", GitWatchParams(project_id=self.project_id), EmptyResult)

    async def graph(self, tab: TabId | None, cursor: str | None, limit: int = _PAGE) -> GitGraphResult:
        params = GitGraphParams(
            project_id=self.project_id, tab_id=tab, cursor=cursor, limit=limit, work_session_id=self.work_session_id,
        )
        return await self.client.request("git/graph", params, GitGraphResult)

    async def summary(self, source: DiffSource) -> DiffSummaryResult:
        params = GitDiffParams(project_id=self.project_id, source=source)
        return await self.client.request("git/diffSummary", params, DiffSummaryResult)

    async def file(self, source: DiffSource, path: str) -> DiffFileResult:
        params = GitDiffFileParams(project_id=self.project_id, source=source, path=path)
        return await self.client.request("git/diffFile", params, DiffFileResult)

    async def facts(self) -> FactsToReviewResult:
        params = GitStateParams(project_id=self.project_id)
        return await self.client.request("git/factsToReview", params, FactsToReviewResult)

    async def stage(self, row: WorktreeGitState, paths: tuple[str, ...]) -> GitChangeResult:
        return await self.client.request("git/stage", self._paths(row, paths), GitChangeResult)

    async def unstage(self, row: WorktreeGitState, paths: tuple[str, ...]) -> GitChangeResult:
        return await self.client.request("git/unstage", self._paths(row, paths), GitChangeResult)

    async def commit(self, row: WorktreeGitState, message: str) -> GitChangeResult:
        params = GitCommitParams(
            project_id=self.project_id,
            worktree=row.path,
            message=message,
            expect_head=row.head_oid,
            expect_index=row.index_digest,
            actor_tab_id=self.actor_tab_id if self.actor_tab_id in row.tab_ids else row.tab_id,
        )
        return await self.client.request("git/commit", params, GitChangeResult)

    async def restore_preview(self, worktree: str, path: str, oid: str) -> RestoreFilePreviewResult:
        params = RestoreFilePreviewParams(project_id=self.project_id, worktree=worktree, path=path, source_oid=oid)
        return await self.client.request("git/restoreFilePreview", params, RestoreFilePreviewResult)

    async def restore(self, worktree: str, preview: RestoreFilePreviewResult) -> GitChangeResult:
        params = RestoreFileParams(
            project_id=self.project_id,
            worktree=worktree,
            path=preview.path,
            source_oid=preview.source_oid,
            confirm_token=preview.confirm_token,
        )
        return await self.client.request("git/restoreFile", params, GitChangeResult)

    async def merge_preview(self, source: str) -> MergePreviewResult:
        params = MergePreviewParams(project_id=self.project_id, source=source)
        return await self.client.request("git/mergePreview", params, MergePreviewResult)

    async def merge(self, preview: MergePreviewResult, mode: MergeMode, message: str | None) -> GitOperationResult:
        params = MergeWorktreeParams(
            project_id=self.project_id,
            source=preview.source,
            target=preview.target,
            source_oid=preview.source_oid,
            target_oid=preview.target_oid,
            mode=mode,
            confirm_token=preview.confirm_token,
            message=message,
        )
        return await self.client.request("git/mergeWorktree", params, GitOperationResult)

    async def archive_session(self, session_id: str) -> WorkSessionView:
        return await self.client.request(
            "workSession/close",
            WorkSessionParams(project_id=self.project_id, work_session_id=session_id),
            WorkSessionView,
        )

    async def cleanup(self, worktree: str, *, discard_ignored: bool) -> CleanupWorktreeResult:
        params = CleanupWorktreeParams(project_id=self.project_id, worktree=worktree, discard_ignored=discard_ignored)
        return await self.client.request("git/cleanupWorktree", params, CleanupWorktreeResult)

    async def rebase(self, row: WorktreeGitState, onto: str) -> GitOperationResult:
        params = RebaseParams(
            project_id=self.project_id, worktree=row.path, onto_oid=onto, expect_head=row.head_oid or ""
        )
        return await self.client.request("git/rebase", params, GitOperationResult)

    async def cherry_pick(self, row: WorktreeGitState, oid: str) -> GitOperationResult:
        params = CherryPickParams(
            project_id=self.project_id, worktree=row.path, oid=oid, expect_head=row.head_oid or ""
        )
        return await self.client.request("git/cherryPick", params, GitOperationResult)

    async def resume(self, worktree: str) -> GitChangeResult:
        params = WorktreeOperationParams(project_id=self.project_id, worktree=worktree)
        return await self.client.request("git/continue", params, GitChangeResult)

    async def abort(self, worktree: str) -> GitChangeResult:
        params = WorktreeOperationParams(project_id=self.project_id, worktree=worktree)
        return await self.client.request("git/abort", params, GitChangeResult)

    async def send_preview(self, source: DiffSource, tab: TabId) -> SendDiffPreviewResult:
        params = SendDiffPreviewParams(project_id=self.project_id, source=source, to_tab=tab)
        return await self.client.request("git/sendDiffPreview", params, SendDiffPreviewResult)

    async def send(self, source: DiffSource, preview: SendDiffPreviewResult) -> CommandResult:
        params = SendDiffParams(
            project_id=self.project_id, source=source, to_tab=preview.to_tab, confirm_token=preview.confirm_token
        )
        return await self.client.request("git/sendDiff", params, CommandResult)

    def _paths(self, row: WorktreeGitState, paths: tuple[str, ...]) -> GitPathsParams:
        return GitPathsParams(
            project_id=self.project_id,
            worktree=row.path,
            paths=paths,
            expect_head=row.head_oid,
            expect_index=row.index_digest,
        )


def worktree_of(state: GitStateResult | None, row: GraphRow) -> WorktreeGitState | None:
    """The worktree this row stands for, as the newest state has it. A clean one is found at its HEAD."""
    if state is None:
        return None
    if row.worktree is not None:
        return next((item for item in state.worktrees if item.path == row.worktree.path), None)
    if row.node is None or row.node.kind != "commit":
        return None
    return next((item for item in state.worktrees if item.head_oid == row.node.oid and not item.is_main), None)


def actions_for(
    worktree: WorktreeGitState | None,
    node: GitNodeView | None,
    *,
    base_branch: str | None,
    has_recipient: bool,
    has_file: bool,
) -> tuple[str, ...]:
    """What a selected row offers. The core checks all of it again; this only hides what cannot work."""
    found: list[str] = []
    if worktree is not None and not worktree.busy:
        found.extend(_worktree_actions(worktree, base_branch))
    if node is not None:
        if worktree is None and _branch_of(node, base_branch) is not None:
            found.append("merge")
        if node.kind == "commit":
            found.append("cherry_pick")
        if has_file:
            found.append("restore")
    if has_recipient:
        found.append("send")
    return tuple(found)


@dataclass(frozen=True, slots=True, kw_only=True)
class RowFlags:
    """What the inspector needs to know of a worktree, in one place."""

    can_commit: bool
    can_merge: bool
    is_merging: bool
    has_conflicts: bool


def row_flags(row: WorktreeGitState | None, base_branch: str | None) -> RowFlags:
    if row is None:
        return RowFlags(can_commit=False, can_merge=False, is_merging=False, has_conflicts=False)
    conflict = row.conflicted > 0 or row.merge.status == "conflict"
    return RowFlags(
        can_commit=row.staged > 0 and not row.busy and row.in_progress is None,
        can_merge=not row.is_main and not row.busy and row.branch not in {None, base_branch} and not conflict,
        is_merging=row.in_progress is not None,
        has_conflicts=conflict,
    )


def tab_git_line(row: WorktreeGitState) -> str:
    """The sidebar's line for a tab: its branch, its plan node, and what merging it would bring."""
    parts = [row.branch or short(row.head_oid)]
    if row.plan_node is not None:
        parts.append(str(row.plan_node))
    line = " · ".join(parts)
    if row.merge.insertions or row.merge.deletions:
        return f"{line}  +{row.merge.insertions} −{row.merge.deletions}"  # noqa: RUF001 -- the minus sign
    return line


def _worktree_actions(row: WorktreeGitState, base_branch: str | None) -> list[str]:
    found: list[str] = []
    if row.in_progress in {"merge", "rebase", "cherry-pick"}:
        found.extend(("continue", "abort"))
    if row.unstaged + row.untracked:
        found.append("stage")
    if row.staged:
        found.append("unstage")
    if row.is_main or row.in_progress is not None:
        return found
    if row.branch is not None and row.branch != base_branch:
        found.append("merge")
        if row.behind > 0:
            found.append("rebase")
    found.append("cleanup")
    return found


def _branch_of(node: GitNodeView, base_branch: str | None) -> str | None:
    return next((ref.name for ref in node.refs if ref.kind == "branch" and ref.name != base_branch), None)


def merge_source(worktree: WorktreeGitState | None, node: GitNodeView | None, base_branch: str | None) -> str | None:
    if worktree is not None:
        return worktree.branch
    return None if node is None else _branch_of(node, base_branch)


def recipients(state: GitStateResult | None, worktree: WorktreeGitState | None) -> tuple[WorktreeGitState, ...]:
    """Tabs a diff may go to: every open one but the tab that made it."""
    rows = () if state is None else state.worktrees
    own = None if worktree is None else worktree.tab_id
    return tuple(row for row in rows if row.tab_id is not None and row.tab_id != own)


def open_operations(state: GitStateResult) -> tuple[GitOperationView, ...]:
    return tuple(item for item in state.operations if item.status in {"started", "conflicted", "merged"})


def format_size(byte_count: int) -> str:
    return f"{byte_count} B" if byte_count < _KIB else f"{byte_count / _KIB:.1f} KiB"


@dataclass(frozen=True, slots=True)
class GitSummary:
    """The sidebar's Git section: where the project's worktrees stand, counted."""

    base_branch: str | None
    worktrees: int
    changed: int
    ready: int
    conflicted: int


def git_summary(state: GitStateResult) -> GitSummary:
    """A worktree with changes has files nobody committed; one that is ready would merge cleanly into the base."""
    agents = [row for row in state.worktrees if not row.is_main]
    return GitSummary(
        base_branch=state.base_branch,
        worktrees=len(agents),
        changed=sum(1 for row in state.worktrees if row.dirty),
        ready=sum(
            1 for row in agents
            if row.ahead > 0 and row_flags(row, state.base_branch).can_merge and row.merge.status == "clean"
        ),
        conflicted=sum(1 for row in state.worktrees if row_flags(row, state.base_branch).has_conflicts),
    )
