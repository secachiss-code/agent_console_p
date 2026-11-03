"""Calls and row data of the Git page. No GTK here."""

from dataclasses import dataclass
from typing import Final, Literal

from aconsole.contracts.app import DiffFileResult, DiffSummaryResult
from aconsole.contracts.common import ProjectId, TabId
from aconsole.contracts.git import (
    DiffSource,
    GitDiffFileParams,
    GitDiffParams,
    GitGraphParams,
    GitGraphResult,
    GitNodeView,
    GitStateParams,
    GitStateResult,
    WorktreeGitState,
)
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
        row for row in state.worktrees if row.dirty and row.head_oid is not None and (tab is None or row.tab_id == tab)
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


class GitViewModel:
    def __init__(self, client: CoreClient, project_id: ProjectId) -> None:
        self.client: CoreClient = client
        self.project_id: ProjectId = project_id

    async def state(self) -> GitStateResult:
        return await self.client.request("git/state", GitStateParams(project_id=self.project_id), GitStateResult)

    async def graph(self, tab: TabId | None, cursor: str | None, limit: int = _PAGE) -> GitGraphResult:
        params = GitGraphParams(project_id=self.project_id, tab_id=tab, cursor=cursor, limit=limit)
        return await self.client.request("git/graph", params, GitGraphResult)

    async def summary(self, source: DiffSource) -> DiffSummaryResult:
        params = GitDiffParams(project_id=self.project_id, source=source)
        return await self.client.request("git/diffSummary", params, DiffSummaryResult)

    async def file(self, source: DiffSource, path: str) -> DiffFileResult:
        params = GitDiffFileParams(project_id=self.project_id, source=source, path=path)
        return await self.client.request("git/diffFile", params, DiffFileResult)
