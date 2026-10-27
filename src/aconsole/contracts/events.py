"""UI event variants. The seq envelope is ipc.EventEnvelope."""

from typing import Literal

import msgspec

from aconsole.contracts.app import (
    AccountView,
    AgentInfo,
    ApprovalView,
    CheckpointView,
    PlanView,
    ProjectView,
    SetupState,
    TabView,
)
from aconsole.contracts.common import ApprovalId, NoteRef, ProjectId, TabId
from aconsole.contracts.errors import ErrorObject


class TabAdded(msgspec.Struct, frozen=True, kw_only=True, tag="tab_added", tag_field="type"):
    tab: TabView


class TabUpdated(msgspec.Struct, frozen=True, kw_only=True, tag="tab_updated", tag_field="type"):
    tab: TabView
    changed: frozenset[str]


class TabRemoved(msgspec.Struct, frozen=True, kw_only=True, tag="tab_removed", tag_field="type"):
    tab_id: TabId


class TabFocusRequested(msgspec.Struct, frozen=True, kw_only=True, tag="tab_focus_requested", tag_field="type"):
    tab_id: TabId


class ApprovalRequested(msgspec.Struct, frozen=True, kw_only=True, tag="approval_requested", tag_field="type"):
    approval: ApprovalView


class ApprovalResolved(msgspec.Struct, frozen=True, kw_only=True, tag="approval_resolved", tag_field="type"):
    approval_id: ApprovalId
    outcome: Literal["allowed", "denied", "expired_to_native", "cancelled"]
    by: Literal["user", "rule", "agent", "timeout"]


class QuotaUpdated(msgspec.Struct, frozen=True, kw_only=True, tag="quota_updated", tag_field="type"):
    account: AccountView


class PlanUpdated(msgspec.Struct, frozen=True, kw_only=True, tag="plan_updated", tag_field="type"):
    plan: PlanView


class ProjectsChanged(msgspec.Struct, frozen=True, kw_only=True, tag="projects_changed", tag_field="type"):
    projects: tuple[ProjectView, ...]
    active_project: ProjectId | None


class AgentsChanged(msgspec.Struct, frozen=True, kw_only=True, tag="agents_changed", tag_field="type"):
    agents: tuple[AgentInfo, ...]


class SettingsChanged(msgspec.Struct, frozen=True, kw_only=True, tag="settings_changed", tag_field="type"):
    rev: int


class Notify(msgspec.Struct, frozen=True, kw_only=True, tag="notify", tag_field="type"):
    tab_id: TabId | None
    title: str
    body: str
    priority: Literal["normal", "high"]
    episode_id: str


class Toast(msgspec.Struct, frozen=True, kw_only=True, tag="toast", tag_field="type"):
    message: str
    action_label: str | None
    action_item_id: str | None
    timeout_s: int = 5


class Banner(msgspec.Struct, frozen=True, kw_only=True, tag="banner", tag_field="type"):
    id: str
    message: str
    button_label: str | None
    button_item_id: str | None
    visible: bool


class InjectToTerminal(msgspec.Struct, frozen=True, kw_only=True, tag="inject_to_terminal", tag_field="type"):
    tab_id: TabId
    text: str
    submit: bool


class CheckpointCreated(msgspec.Struct, frozen=True, kw_only=True, tag="checkpoint_created", tag_field="type"):
    checkpoint: CheckpointView


class OperationProgress(msgspec.Struct, frozen=True, kw_only=True, tag="operation_progress", tag_field="type"):
    operation_id: str
    title: str
    fraction: float | None
    done: bool
    error: ErrorObject | None


class VaultEditRejected(msgspec.Struct, frozen=True, kw_only=True, tag="vault_edit_rejected", tag_field="type"):
    note: NoteRef
    reason: str
    restored: bool


class ScreenTextRequested(msgspec.Struct, frozen=True, kw_only=True, tag="screen_text_requested", tag_field="type"):
    request_id: str
    tab_id: TabId
    last_lines: int


class ResyncRequired(msgspec.Struct, frozen=True, kw_only=True, tag="resync_required", tag_field="type"):
    reason: Literal["backpressure", "restart"]


class SetupRequired(msgspec.Struct, frozen=True, kw_only=True, tag="setup_required", tag_field="type"):
    setup: SetupState


UiEvent = (
    TabAdded
    | TabUpdated
    | TabRemoved
    | TabFocusRequested
    | ApprovalRequested
    | ApprovalResolved
    | QuotaUpdated
    | PlanUpdated
    | ProjectsChanged
    | AgentsChanged
    | SettingsChanged
    | Notify
    | Toast
    | Banner
    | InjectToTerminal
    | CheckpointCreated
    | OperationProgress
    | VaultEditRejected
    | ScreenTextRequested
    | ResyncRequired
    | SetupRequired
)
