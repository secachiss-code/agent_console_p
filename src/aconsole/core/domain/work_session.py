"""A task's workspace outlives the processes that work in it."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

type WorkSessionStatus = Literal["active", "archived"]


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkSession:
    id: str
    project_id: str
    title: str
    worktree: str
    branch: str | None
    base_branch: str | None
    workspace_mode: str
    status: WorkSessionStatus
    created_at: datetime
    updated_at: datetime
    workspace_removed: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkSessionSnapshot:
    work_session_id: str
    sequence: int
    kind: str
    actor_tab_id: str | None
    worktree: str
    ref: str
    head_oid: str | None
    oid: str | None
    tree: str | None
    previous_oid: str | None
    label: str
    status: str
    created_at: datetime
    actor_title: str | None = None
    agent: str | None = None
    plan_node: str | None = None
