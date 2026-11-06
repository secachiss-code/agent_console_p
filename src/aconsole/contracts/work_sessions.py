"""Task sessions own workspaces; agent_session_id still names a CLI conversation."""

from typing import Annotated, Literal

import msgspec
from msgspec import Meta

from aconsole.contracts.common import AwareDatetime, ProjectId, TabId, WorkspaceMode

Title = Annotated[str, Meta(min_length=1, max_length=120)]


class WorkSessionView(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    project_id: ProjectId
    title: str
    worktree: str
    branch: str | None
    base_branch: str | None
    workspace: WorkspaceMode
    status: Literal["active", "archived"]
    created_at: AwareDatetime
    updated_at: AwareDatetime
    workspace_removed: bool = False


class WorkSessionCreateParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    title: Title
    workspace: WorkspaceMode = WorkspaceMode.NEW_WORKTREE
    base_branch: str | None = None
    existing_worktree: str | None = None


class WorkSessionParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    work_session_id: str


class WorkSessionListParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    include_archived: bool = False


class WorkSessionListResult(msgspec.Struct, frozen=True, kw_only=True):
    sessions: tuple[WorkSessionView, ...]


class WorkSessionRenameParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    work_session_id: str
    title: Title


class WorkSessionHandoffParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    work_session_id: str
    to_tab: TabId


class WorkSessionSnapshotView(msgspec.Struct, frozen=True, kw_only=True):
    sequence: int
    kind: str
    actor_tab_id: TabId | None
    worktree: str
    ref: str
    head_oid: str | None
    oid: str | None
    previous_oid: str | None
    label: str
    status: str
    created_at: AwareDatetime
    actor_title: str | None = None
    agent: str | None = None
    plan_node: str | None = None


class WorkSessionHistoryResult(msgspec.Struct, frozen=True, kw_only=True):
    snapshots: tuple[WorkSessionSnapshotView, ...]


class WorkSessionRestorePreviewParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    work_session_id: str
    sequence: int


class WorkSessionRestorePreview(msgspec.Struct, frozen=True, kw_only=True):
    sequence: int
    oid: str
    confirm_token: str


class WorkSessionRestoreParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    work_session_id: str
    sequence: int
    confirm_token: str


class WorkSessionResultParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    work_session_id: str
    tab_id: TabId


class WorkSessionIntegratePreviewParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    work_session_id: str
    sequence: int


class WorkSessionIntegrateParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    work_session_id: str
    sequence: int
    source_oid: str
    target_oid: str
    confirm_token: str


class WorkSessionCleanupWorkerParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    work_session_id: str
    sequence: int
    discard_ignored: bool = False
