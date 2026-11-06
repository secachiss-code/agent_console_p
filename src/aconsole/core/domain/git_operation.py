"""A merge, rebase, or cherry-pick that must be explainable after a crash."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

type OperationKind = Literal["merge", "rebase", "cherry-pick", "cleanup"]
type OperationStatus = Literal["started", "conflicted", "merged", "finished", "failed", "aborted"]
type MergeMode = Literal["fast_forward", "merge", "squash"]

INTERRUPTED = "interrupted by a restart before Git changed the branch"
TARGET_MOVED = "interrupted by a restart, and the branch has moved since"
_OPEN: frozenset[OperationStatus] = frozenset({"started", "conflicted", "merged"})


@dataclass(frozen=True, slots=True, kw_only=True)
class GitOperationRecord:
    id: str
    project_id: str
    kind: OperationKind
    status: OperationStatus
    mode: MergeMode | None
    source: str
    target: str
    source_oid: str
    target_oid: str
    result_oid: str | None
    expected_tree: str | None
    worktree: str | None
    tab_id: str | None
    plan_node: str | None
    message: str | None
    follow_up_pending: bool
    created_at: datetime
    updated_at: datetime
    work_session_id: str | None = None

    @property
    def open(self) -> bool:
        return self.status in _OPEN or self.follow_up_pending

    def moved(
        self,
        at: datetime,
        status: OperationStatus,
        *,
        result_oid: str | None = None,
        message: str | None = None,
        follow_up_pending: bool = False,
    ) -> GitOperationRecord:
        """The next state. An id or message left out stays what it was. A follow-up stays only if it is named."""
        return replace(
            self,
            status=status,
            updated_at=at,
            result_oid=result_oid or self.result_oid,
            message=message or self.message,
            follow_up_pending=follow_up_pending,
        )


def commit_trailers(*, node: str | None, agent: str | None, tab: str | None) -> tuple[tuple[str, str], ...]:
    """What ties a commit to its work once the tab and the branch are gone."""
    named = (("AConsole-Node", node), ("AConsole-Agent", agent), ("AConsole-Tab", tab))
    return tuple((key, value) for key, value in named if value)


def merge_modes(*, clean: bool, already_merged: bool, can_fast_forward: bool) -> tuple[MergeMode, ...]:
    """What may be done with exactly these two commits. A squash of a fast-forward is still a squash."""
    if already_merged or not clean:
        return ()
    if can_fast_forward:
        return ("fast_forward", "merge", "squash")
    return ("merge", "squash")


def landed(record: GitOperationRecord, *, head: str, parents: tuple[str, ...], tree: str | None) -> bool:
    """Whether a merge that did not get to say so did reach its branch. Only the exact result counts."""
    if record.mode == "fast_forward":
        return head == record.source_oid
    if record.mode == "merge":
        return parents == (record.target_oid, record.source_oid)
    return parents == (record.target_oid,) and tree is not None and tree == record.expected_tree
