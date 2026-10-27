"""Application read-models and method payloads. Transport envelopes live in ipc."""

from typing import Annotated, Literal

import msgspec
from msgspec import Meta

from aconsole.contracts.common import (
    AccountId,
    AgentKind,
    ApprovalDecision,
    ApprovalId,
    ApprovalScope,
    AwareDatetime,
    CheckpointId,
    NodeId,
    NodeStatus,
    NoteRef,
    PaletteKind,
    Percent,
    PlanId,
    ProjectId,
    QuotaLevel,
    RiskTag,
    TabId,
    TabStatus,
    WorkspaceMode,
)
from aconsole.contracts.settings import SettingsPatch


class QuotaWindowView(msgspec.Struct, frozen=True, kw_only=True):
    key: Literal["5h", "7d", "week", "month", "budget"]
    used_pct: Percent | None
    resets_at: AwareDatetime | None
    level: QuotaLevel
    observed_at: AwareDatetime | None
    estimated: bool


class AccountView(msgspec.Struct, frozen=True, kw_only=True):
    id: AccountId
    agent: AgentKind
    plan_label: str
    quota: tuple[QuotaWindowView, ...]


class AgentInfo(msgspec.Struct, frozen=True, kw_only=True):
    kind: AgentKind
    display_name: str
    container: str
    available: bool
    unavailable_reason: str | None
    logged_in: bool | None
    version: str | None
    supported: bool
    capabilities: frozenset[str]


class ProjectView(msgspec.Struct, frozen=True, kw_only=True):
    id: ProjectId
    name: str
    root: str
    default_branch: str
    vault_folder: NoteRef | None
    languages: frozenset[str]


class TabView(msgspec.Struct, frozen=True, kw_only=True):
    id: TabId
    project_id: ProjectId
    agent: AgentKind
    account_id: AccountId | None
    title: str
    order: int
    status: TabStatus
    status_detail: str | None
    status_since: AwareDatetime
    attention: bool
    last_message: str | None
    worktree_path: str
    branch: str | None
    base_branch: str | None
    worktree_dirty: bool
    ahead: int
    behind: int
    plan_id: PlanId | None
    plan_node: NodeId | None
    plan_node_title: str | None
    context_used_pct: Percent | None
    session_cost_usd: float | None
    agent_session_id: str | None
    pending_approvals: int
    checkpoints: int
    started: bool


class CommandDetail(msgspec.Struct, frozen=True, kw_only=True, tag="command", tag_field="type"):
    command: str
    args_preview: str | None


class EditDetail(msgspec.Struct, frozen=True, kw_only=True, tag="edit", tag_field="type"):
    files: tuple[str, ...]
    diff_preview: str | None


class GenericDetail(msgspec.Struct, frozen=True, kw_only=True, tag="generic", tag_field="type"):
    json_preview: str


ApprovalDetail = CommandDetail | EditDetail | GenericDetail


class ApprovalView(msgspec.Struct, frozen=True, kw_only=True):
    id: ApprovalId
    nonce: str
    tab_id: TabId
    agent: AgentKind
    tool: str
    summary: str
    detail: ApprovalDetail
    cwd: str
    risk: frozenset[RiskTag]
    requested_at: AwareDatetime
    expires_at: AwareDatetime
    allow_tab_session: bool


class PlanNodeView(msgspec.Struct, frozen=True, kw_only=True):
    id: NodeId
    key: str
    title: str
    status: NodeStatus
    ready: bool
    layer: int
    priority: float
    depends_on: tuple[NodeId, ...]
    owner_agent: AgentKind | None
    tab_id: TabId | None
    lease_expires_at: AwareDatetime | None
    note: NoteRef | None


class PlanView(msgspec.Struct, frozen=True, kw_only=True):
    id: PlanId
    project_id: ProjectId
    title: str
    goal: str
    status: Literal["active", "completed", "archived"]
    nodes: tuple[PlanNodeView, ...]
    done: int
    total: int
    canvas: NoteRef | None
    updated_at: AwareDatetime


class SetupState(msgspec.Struct, frozen=True, kw_only=True):
    required: bool
    failing_checks: int
    warnings: int


class AppSnapshot(msgspec.Struct, frozen=True, kw_only=True):
    api_version: str
    projects: tuple[ProjectView, ...]
    active_project: ProjectId | None
    agents: tuple[AgentInfo, ...]
    accounts: tuple[AccountView, ...]
    tabs: tuple[TabView, ...]
    approvals: tuple[ApprovalView, ...]
    plans: tuple[PlanView, ...]
    vault_ok: bool
    setup: SetupState
    settings_rev: int


class TerminalLaunchSpec(msgspec.Struct, frozen=True, kw_only=True):
    argv: tuple[str, ...]
    env: dict[str, str]
    cwd: str
    scrollback_lines: int
    title: str


class EmptyParams(msgspec.Struct, frozen=True, kw_only=True):
    pass


class EmptyResult(msgspec.Struct, frozen=True, kw_only=True):
    pass


class SnapshotResult(msgspec.Struct, frozen=True, kw_only=True):
    snapshot: AppSnapshot
    seq: int


class SubscribeParams(msgspec.Struct, frozen=True, kw_only=True):
    after_seq: int


class SubscribeResult(msgspec.Struct, frozen=True, kw_only=True):
    current_seq: int


class ProjectAddParams(msgspec.Struct, frozen=True, kw_only=True):
    root: str


class ProjectIdParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId


class TabIdParams(msgspec.Struct, frozen=True, kw_only=True):
    tab_id: TabId


class OpenTabParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    agent: AgentKind
    workspace: WorkspaceMode
    base_branch: str | None = None
    worktree_name: str | None = None
    existing_worktree: str | None = None
    plan_node: NodeId | None = None
    resume_session: str | None = None
    fork: bool = False
    title: str | None = None
    start_now: bool = True


class StartedParams(msgspec.Struct, frozen=True, kw_only=True):
    tab_id: TabId
    pid: int


class ExitedParams(msgspec.Struct, frozen=True, kw_only=True):
    tab_id: TabId
    wait_status: int


class CloseTabParams(msgspec.Struct, frozen=True, kw_only=True):
    tab_id: TabId
    decision: Literal["keep", "remove"] | None = None


class CloseTabResult(msgspec.Struct, frozen=True, kw_only=True):
    closed: bool
    needs_decision: bool
    dirty_summary: str | None


class RestartParams(msgspec.Struct, frozen=True, kw_only=True):
    tab_id: TabId
    resume: bool


class RenameParams(msgspec.Struct, frozen=True, kw_only=True):
    tab_id: TabId
    title: Annotated[str, Meta(min_length=1, max_length=80)]


class ReorderParams(msgspec.Struct, frozen=True, kw_only=True):
    tab_id: TabId
    order: int


class FocusParams(msgspec.Struct, frozen=True, kw_only=True):
    tab_id: TabId | None
    window_active: bool


class DecideParams(msgspec.Struct, frozen=True, kw_only=True):
    approval_id: ApprovalId
    nonce: str
    decision: ApprovalDecision
    scope: ApprovalScope = ApprovalScope.ONCE
    message: str | None = None


class AppliedResult(msgspec.Struct, frozen=True, kw_only=True):
    applied: bool


class ReleaseParams(msgspec.Struct, frozen=True, kw_only=True):
    approval_id: ApprovalId
    nonce: str


class ReleasedResult(msgspec.Struct, frozen=True, kw_only=True):
    released: bool


class PaletteArgument(msgspec.Struct, frozen=True, kw_only=True):
    label: str
    choices: tuple[tuple[str, str], ...] | None


class PaletteItem(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    kind: PaletteKind
    title: str
    subtitle: str | None
    keywords: tuple[str, ...]
    aliases: tuple[str, ...]
    score_hint: float
    enabled: bool
    disabled_reason: str | None
    argument: PaletteArgument | None
    shortcut: str | None


class PaletteQuery(msgspec.Struct, frozen=True, kw_only=True):
    tab_id: TabId | None
    project_id: ProjectId | None


class PaletteItemsResult(msgspec.Struct, frozen=True, kw_only=True):
    items: tuple[PaletteItem, ...]


class PaletteExecuteParams(msgspec.Struct, frozen=True, kw_only=True):
    item_id: str
    tab_id: TabId | None
    project_id: ProjectId | None
    argument: str | None


class PageRef(msgspec.Struct, frozen=True, kw_only=True):
    kind: Literal["plan", "impact", "diff", "checkpoints", "sessions"]
    ref: str


class CommandResult(msgspec.Struct, frozen=True, kw_only=True):
    ok: bool
    toast: str | None
    opened_tab: TabId | None = None
    open_page: PageRef | None = None
    open_uri: str | None = None


class ResetRankingParams(msgspec.Struct, frozen=True, kw_only=True):
    item_id: str | None


class SessionInfo(msgspec.Struct, frozen=True, kw_only=True):
    session_id: str
    title: str
    started_at: AwareDatetime
    updated_at: AwareDatetime
    cwd: str
    branch: str | None
    preview: str | None


class SessionListParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    agent: AgentKind


class SessionListResult(msgspec.Struct, frozen=True, kw_only=True):
    sessions: tuple[SessionInfo, ...]


class PlanListParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    status: Literal["active", "all"] = "active"


class PlanListResult(msgspec.Struct, frozen=True, kw_only=True):
    plans: tuple[PlanView, ...]


class PlanIdParams(msgspec.Struct, frozen=True, kw_only=True):
    plan_id: PlanId


class SetNodeStatusParams(msgspec.Struct, frozen=True, kw_only=True):
    node_id: NodeId
    status: NodeStatus
    note: str | None = None


class ImpactParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    target: str
    hops: Annotated[int, Meta(ge=1, le=3)] = 3
    kinds: frozenset[str] | None = None


class ImpactView(msgspec.Struct, frozen=True, kw_only=True):
    target: str
    hops: int
    layers: tuple[tuple[str, ...], ...]
    truncated: bool
    canvas: NoteRef | None


class NoteUriParams(msgspec.Struct, frozen=True, kw_only=True):
    note: NoteRef


class NoteUriResult(msgspec.Struct, frozen=True, kw_only=True):
    uri: str


class CheckpointView(msgspec.Struct, frozen=True, kw_only=True):
    id: CheckpointId
    tab_id: TabId
    turn: int
    created_at: AwareDatetime
    files_changed: int
    insertions: int
    deletions: int
    label: str


class CheckpointListParams(msgspec.Struct, frozen=True, kw_only=True):
    tab_id: TabId


class CheckpointListResult(msgspec.Struct, frozen=True, kw_only=True):
    checkpoints: tuple[CheckpointView, ...]


class CheckpointIdParams(msgspec.Struct, frozen=True, kw_only=True):
    checkpoint_id: CheckpointId


class RestorePreviewResult(msgspec.Struct, frozen=True, kw_only=True):
    diffstat: str
    files: tuple[str, ...]
    confirm_token: str
    scope_note: str


class RestoreParams(msgspec.Struct, frozen=True, kw_only=True):
    checkpoint_id: CheckpointId
    confirm_token: str


class RestoreResult(msgspec.Struct, frozen=True, kw_only=True):
    restored: bool
    safety_checkpoint: CheckpointId


class DiffFileView(msgspec.Struct, frozen=True, kw_only=True):
    path: str
    old_path: str | None
    change: Literal["added", "modified", "deleted", "renamed", "binary"]
    insertions: int
    deletions: int


class DiffLine(msgspec.Struct, frozen=True, kw_only=True):
    kind: Literal["context", "add", "del", "meta"]
    old_no: int | None
    new_no: int | None
    text: str
    word_spans: tuple[tuple[int, int], ...] = ()


class DiffHunk(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    header: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: tuple[DiffLine, ...]


class DiffSummaryParams(msgspec.Struct, frozen=True, kw_only=True):
    tab_id: TabId
    base: str | None = None


class DiffSummaryResult(msgspec.Struct, frozen=True, kw_only=True):
    base: str
    head: str
    files: tuple[DiffFileView, ...]
    insertions: int
    deletions: int


class DiffFileParams(msgspec.Struct, frozen=True, kw_only=True):
    tab_id: TabId
    path: str
    base: str | None = None
    max_lines: int = 2000


class DiffFileResult(msgspec.Struct, frozen=True, kw_only=True):
    hunks: tuple[DiffHunk, ...]
    truncated: bool


class SendCommentParams(msgspec.Struct, frozen=True, kw_only=True):
    tab_id: TabId
    path: str
    hunk_id: str
    text: str


class CheckResult(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    title: str
    ok: bool
    severity: Literal["error", "warning", "info"]
    detail: str | None
    fix_id: str | None
    requires_confirmation: bool


class ChecksResult(msgspec.Struct, frozen=True, kw_only=True):
    checks: tuple[CheckResult, ...]


class FixIdParams(msgspec.Struct, frozen=True, kw_only=True):
    fix_id: str


class FixPreview(msgspec.Struct, frozen=True, kw_only=True):
    fix_id: str
    summary: str
    diff: str | None
    commands: tuple[str, ...]
    reversible: bool
    confirm_token: str
    expires_at: AwareDatetime


class ApplyFixParams(msgspec.Struct, frozen=True, kw_only=True):
    fix_id: str
    confirm_token: str


class SettingsUpdateParams(msgspec.Struct, frozen=True, kw_only=True):
    patch: SettingsPatch
    rev: int


class QuitCheckResult(msgspec.Struct, frozen=True, kw_only=True):
    busy_tabs: tuple[TabId, ...]
    pending_approvals: int


class ScreenTextReplyParams(msgspec.Struct, frozen=True, kw_only=True):
    request_id: str
    tab_id: TabId
    lines: tuple[str, ...]
