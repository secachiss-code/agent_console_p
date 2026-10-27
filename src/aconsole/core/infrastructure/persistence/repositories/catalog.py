"""Row records and repositories. ``to_domain`` / ``from_domain`` copy every column."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from aconsole.core.domain.agent import AgentKind
from aconsole.core.domain.plan import NodeStatus
from aconsole.core.domain.tab_status import TabStatus
from aconsole.core.domain.workspace import WorkspaceMode
from aconsole.core.infrastructure.persistence.models import (
    AgentAccount,
    AgentSession,
    Approval,
    ApprovalRule,
    ApprovalStatus,
    ArtifactKind,
    Base,
    CanvasPosition,
    Checkpoint,
    EdgeKind,
    EventLog,
    EventSource,
    Frecency,
    Handover,
    Iteration,
    KbEdge,
    KbEdgeKind,
    KbNode,
    KbType,
    Lease,
    NodeArtifact,
    Plan,
    PlanEdge,
    PlanNode,
    PlanStatus,
    Project,
    QuotaCurrent,
    QuotaHistory,
    Setting,
    Tab,
    VaultFile,
    Workspace,
)


@dataclass(frozen=True, kw_only=True)
class ProjectRecord:
    id: str
    name: str
    root_path: str
    default_branch: str
    vault_folder: str | None
    languages: list[str]
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class AgentAccountRecord:
    id: str
    agent_kind: AgentKind
    plan_label: str
    container_name: str


@dataclass(frozen=True, kw_only=True)
class WorkspaceRecord:
    id: str
    project_id: str
    mode: WorkspaceMode
    path: str
    branch: str | None
    base_branch: str | None
    base_commit: str | None
    locked: bool
    dirty_cached: bool
    created_at: datetime
    removed_at: datetime | None


@dataclass(frozen=True, kw_only=True)
class PlanRecord:
    id: str
    project_id: str
    title: str
    goal: str
    status: PlanStatus
    canvas_note_ref: str | None
    created_by_tab: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, kw_only=True)
class PlanNodeRecord:
    id: str
    plan_id: str
    node_key: str
    title: str
    objective: str
    status: NodeStatus
    owner_agent: AgentKind | None
    paths: list[str]
    acceptance: list[str]
    test_cmd: str | None
    review_required: bool
    evidence: str | None
    note_ref: str | None
    layer: int
    priority: float
    unmet_blockers: int
    fence_seq: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, kw_only=True)
class TabRecord:
    id: str
    project_id: str
    workspace_id: str | None
    agent_kind: AgentKind
    account_id: str | None
    title: str
    tab_order: int
    last_status: TabStatus
    status_detail: str | None
    plan_node_id: str | None
    context_used_pct: float | None
    session_cost_usd: float | None
    agent_session_id: str | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, kw_only=True)
class AgentSessionRecord:
    id: str
    tab_id: str
    agent_session_id: str
    transcript_path: str | None
    started_at: datetime
    ended_at: datetime | None


@dataclass(frozen=True, kw_only=True)
class EventLogRecord:
    id: int | None = None
    tab_id: str
    source: EventSource
    event_type: str
    payload: dict[str, object]
    at: datetime


@dataclass(frozen=True, kw_only=True)
class QuotaCurrentRecord:
    account_id: str
    window_key: str
    used_pct: float | None
    resets_at: datetime | None
    estimated: bool
    source: str
    observed_at: datetime


@dataclass(frozen=True, kw_only=True)
class QuotaHistoryRecord:
    id: int | None = None
    account_id: str
    window_key: str
    used_pct: float
    observed_at: datetime


@dataclass(frozen=True, kw_only=True)
class ApprovalRecord:
    id: str
    tab_id: str
    agent_session_id: str
    tool: str
    input_sha256: str
    nonce: str
    summary: str
    risk: list[str]
    status: ApprovalStatus
    decided_by: str | None
    requested_at: datetime
    decided_at: datetime | None
    expires_at: datetime


@dataclass(frozen=True, kw_only=True)
class ApprovalRuleRecord:
    id: str
    tab_id: str
    tool: str
    command_prefix: str | None
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class CheckpointRecord:
    id: str
    tab_id: str
    turn: int
    git_ref: str
    tree_sha: str
    label: str
    files_changed: int
    insertions: int
    deletions: int
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class PlanEdgeRecord:
    src_node_id: str
    dst_node_id: str
    kind: EdgeKind


@dataclass(frozen=True, kw_only=True)
class LeaseRecord:
    node_id: str
    lease_id: str
    tab_id: str
    fence: int
    acquired_at: datetime
    renewed_at: datetime
    expires_at: datetime


@dataclass(frozen=True, kw_only=True)
class NodeArtifactRecord:
    id: str
    node_id: str
    kind: ArtifactKind
    ref: str
    title: str | None
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class CanvasPositionRecord:
    plan_id: str
    node_id: str
    x: int
    y: int
    user_moved: bool
    updated_at: datetime


@dataclass(frozen=True, kw_only=True)
class KbNodeRecord:
    id: str
    project_id: str
    node_type: KbType
    title: str
    note_ref: str | None
    status: str | None
    source: str | None
    verified_commit: str | None
    is_stale: bool
    updated_at: datetime


@dataclass(frozen=True, kw_only=True)
class KbEdgeRecord:
    src_id: str
    dst_id: str
    kind: KbEdgeKind
    origin: str


@dataclass(frozen=True, kw_only=True)
class VaultFileRecord:
    note_ref: str
    kind: str
    owner: str
    kb_id: str | None
    plan_node_id: str | None
    frontmatter_hash: str | None
    body_hash: str | None
    base_frontmatter: dict[str, object] | None
    mtime_ns: int | None
    size: int | None
    inode: int | None
    last_writer: str
    pending_write: dict[str, object] | None
    synced_at: datetime


@dataclass(frozen=True, kw_only=True)
class HandoverRecord:
    id: str
    project_id: str
    source_tab_id: str | None
    target_agent: AgentKind | None
    plan_node_id: str | None
    summary_markdown: str
    note_ref: str | None
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class IterationRecord:
    project_id: str
    number: int
    dag_state_hash: str
    manifest_note_ref: str
    type_check_passed: bool
    created_at: datetime


@dataclass(frozen=True, kw_only=True)
class FrecencyRecord:
    item_id: str
    score: float
    last_used: datetime
    uses: int


@dataclass(frozen=True, kw_only=True)
class SettingRecord:
    key: str
    value: dict[str, object]
    rev: int


def _persist(session: Session, row: Base) -> None:
    session.add(row)
    session.flush()


class ProjectRepository:
    def to_domain(self, row: Project) -> ProjectRecord:
        return ProjectRecord(
            id=row.id,
            name=row.name,
            root_path=row.root_path,
            default_branch=row.default_branch,
            vault_folder=row.vault_folder,
            languages=row.languages,
            created_at=row.created_at,
        )

    def from_domain(self, record: ProjectRecord) -> Project:
        return Project(
            id=record.id,
            name=record.name,
            root_path=record.root_path,
            default_branch=record.default_branch,
            vault_folder=record.vault_folder,
            languages=record.languages,
            created_at=record.created_at,
        )

    def identity(self, record: ProjectRecord) -> object:
        return record.id

    def add(self, session: Session, record: ProjectRecord) -> ProjectRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> ProjectRecord:
        found = session.get(Project, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class AgentAccountRepository:
    def to_domain(self, row: AgentAccount) -> AgentAccountRecord:
        return AgentAccountRecord(
            id=row.id,
            agent_kind=row.agent_kind,
            plan_label=row.plan_label,
            container_name=row.container_name,
        )

    def from_domain(self, record: AgentAccountRecord) -> AgentAccount:
        return AgentAccount(
            id=record.id,
            agent_kind=record.agent_kind,
            plan_label=record.plan_label,
            container_name=record.container_name,
        )

    def identity(self, record: AgentAccountRecord) -> object:
        return record.id

    def add(self, session: Session, record: AgentAccountRecord) -> AgentAccountRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> AgentAccountRecord:
        found = session.get(AgentAccount, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class WorkspaceRepository:
    def to_domain(self, row: Workspace) -> WorkspaceRecord:
        return WorkspaceRecord(
            id=row.id,
            project_id=row.project_id,
            mode=row.mode,
            path=row.path,
            branch=row.branch,
            base_branch=row.base_branch,
            base_commit=row.base_commit,
            locked=row.locked,
            dirty_cached=row.dirty_cached,
            created_at=row.created_at,
            removed_at=row.removed_at,
        )

    def from_domain(self, record: WorkspaceRecord) -> Workspace:
        return Workspace(
            id=record.id,
            project_id=record.project_id,
            mode=record.mode,
            path=record.path,
            branch=record.branch,
            base_branch=record.base_branch,
            base_commit=record.base_commit,
            locked=record.locked,
            dirty_cached=record.dirty_cached,
            created_at=record.created_at,
            removed_at=record.removed_at,
        )

    def identity(self, record: WorkspaceRecord) -> object:
        return record.id

    def add(self, session: Session, record: WorkspaceRecord) -> WorkspaceRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> WorkspaceRecord:
        found = session.get(Workspace, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class PlanRepository:
    def to_domain(self, row: Plan) -> PlanRecord:
        return PlanRecord(
            id=row.id,
            project_id=row.project_id,
            title=row.title,
            goal=row.goal,
            status=row.status,
            canvas_note_ref=row.canvas_note_ref,
            created_by_tab=row.created_by_tab,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def from_domain(self, record: PlanRecord) -> Plan:
        return Plan(
            id=record.id,
            project_id=record.project_id,
            title=record.title,
            goal=record.goal,
            status=record.status,
            canvas_note_ref=record.canvas_note_ref,
            created_by_tab=record.created_by_tab,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def identity(self, record: PlanRecord) -> object:
        return record.id

    def add(self, session: Session, record: PlanRecord) -> PlanRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> PlanRecord:
        found = session.get(Plan, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class PlanNodeRepository:
    def to_domain(self, row: PlanNode) -> PlanNodeRecord:
        return PlanNodeRecord(
            id=row.id,
            plan_id=row.plan_id,
            node_key=row.node_key,
            title=row.title,
            objective=row.objective,
            status=row.status,
            owner_agent=row.owner_agent,
            paths=row.paths,
            acceptance=row.acceptance,
            test_cmd=row.test_cmd,
            review_required=row.review_required,
            evidence=row.evidence,
            note_ref=row.note_ref,
            layer=row.layer,
            priority=row.priority,
            unmet_blockers=row.unmet_blockers,
            fence_seq=row.fence_seq,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def from_domain(self, record: PlanNodeRecord) -> PlanNode:
        return PlanNode(
            id=record.id,
            plan_id=record.plan_id,
            node_key=record.node_key,
            title=record.title,
            objective=record.objective,
            status=record.status,
            owner_agent=record.owner_agent,
            paths=record.paths,
            acceptance=record.acceptance,
            test_cmd=record.test_cmd,
            review_required=record.review_required,
            evidence=record.evidence,
            note_ref=record.note_ref,
            layer=record.layer,
            priority=record.priority,
            unmet_blockers=record.unmet_blockers,
            fence_seq=record.fence_seq,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def identity(self, record: PlanNodeRecord) -> object:
        return record.id

    def add(self, session: Session, record: PlanNodeRecord) -> PlanNodeRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> PlanNodeRecord:
        found = session.get(PlanNode, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class TabRepository:
    def to_domain(self, row: Tab) -> TabRecord:
        return TabRecord(
            id=row.id,
            project_id=row.project_id,
            workspace_id=row.workspace_id,
            agent_kind=row.agent_kind,
            account_id=row.account_id,
            title=row.title,
            tab_order=row.tab_order,
            last_status=row.last_status,
            status_detail=row.status_detail,
            plan_node_id=row.plan_node_id,
            context_used_pct=row.context_used_pct,
            session_cost_usd=row.session_cost_usd,
            agent_session_id=row.agent_session_id,
            closed_at=row.closed_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def from_domain(self, record: TabRecord) -> Tab:
        return Tab(
            id=record.id,
            project_id=record.project_id,
            workspace_id=record.workspace_id,
            agent_kind=record.agent_kind,
            account_id=record.account_id,
            title=record.title,
            tab_order=record.tab_order,
            last_status=record.last_status,
            status_detail=record.status_detail,
            plan_node_id=record.plan_node_id,
            context_used_pct=record.context_used_pct,
            session_cost_usd=record.session_cost_usd,
            agent_session_id=record.agent_session_id,
            closed_at=record.closed_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def identity(self, record: TabRecord) -> object:
        return record.id

    def add(self, session: Session, record: TabRecord) -> TabRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> TabRecord:
        found = session.get(Tab, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class AgentSessionRepository:
    def to_domain(self, row: AgentSession) -> AgentSessionRecord:
        return AgentSessionRecord(
            id=row.id,
            tab_id=row.tab_id,
            agent_session_id=row.agent_session_id,
            transcript_path=row.transcript_path,
            started_at=row.started_at,
            ended_at=row.ended_at,
        )

    def from_domain(self, record: AgentSessionRecord) -> AgentSession:
        return AgentSession(
            id=record.id,
            tab_id=record.tab_id,
            agent_session_id=record.agent_session_id,
            transcript_path=record.transcript_path,
            started_at=record.started_at,
            ended_at=record.ended_at,
        )

    def identity(self, record: AgentSessionRecord) -> object:
        return record.id

    def add(self, session: Session, record: AgentSessionRecord) -> AgentSessionRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> AgentSessionRecord:
        found = session.get(AgentSession, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class EventLogRepository:
    def to_domain(self, row: EventLog) -> EventLogRecord:
        return EventLogRecord(
            id=row.id,
            tab_id=row.tab_id,
            source=row.source,
            event_type=row.event_type,
            payload=row.payload,
            at=row.at,
        )

    def from_domain(self, record: EventLogRecord) -> EventLog:
        row = EventLog(
            tab_id=record.tab_id,
            source=record.source,
            event_type=record.event_type,
            payload=record.payload,
            at=record.at,
        )
        if record.id is not None:
            row.id = record.id
        return row

    def identity(self, record: EventLogRecord) -> object:
        return record.id

    def add(self, session: Session, record: EventLogRecord) -> EventLogRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> EventLogRecord:
        found = session.get(EventLog, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class QuotaCurrentRepository:
    def to_domain(self, row: QuotaCurrent) -> QuotaCurrentRecord:
        return QuotaCurrentRecord(
            account_id=row.account_id,
            window_key=row.window_key,
            used_pct=row.used_pct,
            resets_at=row.resets_at,
            estimated=row.estimated,
            source=row.source,
            observed_at=row.observed_at,
        )

    def from_domain(self, record: QuotaCurrentRecord) -> QuotaCurrent:
        return QuotaCurrent(
            account_id=record.account_id,
            window_key=record.window_key,
            used_pct=record.used_pct,
            resets_at=record.resets_at,
            estimated=record.estimated,
            source=record.source,
            observed_at=record.observed_at,
        )

    def identity(self, record: QuotaCurrentRecord) -> tuple[object, ...]:
        return (record.account_id, record.window_key)

    def add(self, session: Session, record: QuotaCurrentRecord) -> QuotaCurrentRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> QuotaCurrentRecord:
        found = session.get(QuotaCurrent, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class QuotaHistoryRepository:
    def to_domain(self, row: QuotaHistory) -> QuotaHistoryRecord:
        return QuotaHistoryRecord(
            id=row.id,
            account_id=row.account_id,
            window_key=row.window_key,
            used_pct=row.used_pct,
            observed_at=row.observed_at,
        )

    def from_domain(self, record: QuotaHistoryRecord) -> QuotaHistory:
        row = QuotaHistory(
            account_id=record.account_id,
            window_key=record.window_key,
            used_pct=record.used_pct,
            observed_at=record.observed_at,
        )
        if record.id is not None:
            row.id = record.id
        return row

    def identity(self, record: QuotaHistoryRecord) -> object:
        return record.id

    def add(self, session: Session, record: QuotaHistoryRecord) -> QuotaHistoryRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> QuotaHistoryRecord:
        found = session.get(QuotaHistory, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class ApprovalRepository:
    def to_domain(self, row: Approval) -> ApprovalRecord:
        return ApprovalRecord(
            id=row.id,
            tab_id=row.tab_id,
            agent_session_id=row.agent_session_id,
            tool=row.tool,
            input_sha256=row.input_sha256,
            nonce=row.nonce,
            summary=row.summary,
            risk=row.risk,
            status=row.status,
            decided_by=row.decided_by,
            requested_at=row.requested_at,
            decided_at=row.decided_at,
            expires_at=row.expires_at,
        )

    def from_domain(self, record: ApprovalRecord) -> Approval:
        return Approval(
            id=record.id,
            tab_id=record.tab_id,
            agent_session_id=record.agent_session_id,
            tool=record.tool,
            input_sha256=record.input_sha256,
            nonce=record.nonce,
            summary=record.summary,
            risk=record.risk,
            status=record.status,
            decided_by=record.decided_by,
            requested_at=record.requested_at,
            decided_at=record.decided_at,
            expires_at=record.expires_at,
        )

    def identity(self, record: ApprovalRecord) -> object:
        return record.id

    def add(self, session: Session, record: ApprovalRecord) -> ApprovalRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> ApprovalRecord:
        found = session.get(Approval, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class ApprovalRuleRepository:
    def to_domain(self, row: ApprovalRule) -> ApprovalRuleRecord:
        return ApprovalRuleRecord(
            id=row.id,
            tab_id=row.tab_id,
            tool=row.tool,
            command_prefix=row.command_prefix,
            created_at=row.created_at,
        )

    def from_domain(self, record: ApprovalRuleRecord) -> ApprovalRule:
        return ApprovalRule(
            id=record.id,
            tab_id=record.tab_id,
            tool=record.tool,
            command_prefix=record.command_prefix,
            created_at=record.created_at,
        )

    def identity(self, record: ApprovalRuleRecord) -> object:
        return record.id

    def add(self, session: Session, record: ApprovalRuleRecord) -> ApprovalRuleRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> ApprovalRuleRecord:
        found = session.get(ApprovalRule, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class CheckpointRepository:
    def to_domain(self, row: Checkpoint) -> CheckpointRecord:
        return CheckpointRecord(
            id=row.id,
            tab_id=row.tab_id,
            turn=row.turn,
            git_ref=row.git_ref,
            tree_sha=row.tree_sha,
            label=row.label,
            files_changed=row.files_changed,
            insertions=row.insertions,
            deletions=row.deletions,
            created_at=row.created_at,
        )

    def from_domain(self, record: CheckpointRecord) -> Checkpoint:
        return Checkpoint(
            id=record.id,
            tab_id=record.tab_id,
            turn=record.turn,
            git_ref=record.git_ref,
            tree_sha=record.tree_sha,
            label=record.label,
            files_changed=record.files_changed,
            insertions=record.insertions,
            deletions=record.deletions,
            created_at=record.created_at,
        )

    def identity(self, record: CheckpointRecord) -> object:
        return record.id

    def add(self, session: Session, record: CheckpointRecord) -> CheckpointRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> CheckpointRecord:
        found = session.get(Checkpoint, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class PlanEdgeRepository:
    def to_domain(self, row: PlanEdge) -> PlanEdgeRecord:
        return PlanEdgeRecord(
            src_node_id=row.src_node_id,
            dst_node_id=row.dst_node_id,
            kind=row.kind,
        )

    def from_domain(self, record: PlanEdgeRecord) -> PlanEdge:
        return PlanEdge(
            src_node_id=record.src_node_id,
            dst_node_id=record.dst_node_id,
            kind=record.kind,
        )

    def identity(self, record: PlanEdgeRecord) -> tuple[object, ...]:
        return (record.src_node_id, record.dst_node_id, record.kind)

    def add(self, session: Session, record: PlanEdgeRecord) -> PlanEdgeRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> PlanEdgeRecord:
        found = session.get(PlanEdge, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class LeaseRepository:
    def to_domain(self, row: Lease) -> LeaseRecord:
        return LeaseRecord(
            node_id=row.node_id,
            lease_id=row.lease_id,
            tab_id=row.tab_id,
            fence=row.fence,
            acquired_at=row.acquired_at,
            renewed_at=row.renewed_at,
            expires_at=row.expires_at,
        )

    def from_domain(self, record: LeaseRecord) -> Lease:
        return Lease(
            node_id=record.node_id,
            lease_id=record.lease_id,
            tab_id=record.tab_id,
            fence=record.fence,
            acquired_at=record.acquired_at,
            renewed_at=record.renewed_at,
            expires_at=record.expires_at,
        )

    def identity(self, record: LeaseRecord) -> object:
        return record.node_id

    def add(self, session: Session, record: LeaseRecord) -> LeaseRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> LeaseRecord:
        found = session.get(Lease, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class NodeArtifactRepository:
    def to_domain(self, row: NodeArtifact) -> NodeArtifactRecord:
        return NodeArtifactRecord(
            id=row.id,
            node_id=row.node_id,
            kind=row.kind,
            ref=row.ref,
            title=row.title,
            created_at=row.created_at,
        )

    def from_domain(self, record: NodeArtifactRecord) -> NodeArtifact:
        return NodeArtifact(
            id=record.id,
            node_id=record.node_id,
            kind=record.kind,
            ref=record.ref,
            title=record.title,
            created_at=record.created_at,
        )

    def identity(self, record: NodeArtifactRecord) -> object:
        return record.id

    def add(self, session: Session, record: NodeArtifactRecord) -> NodeArtifactRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> NodeArtifactRecord:
        found = session.get(NodeArtifact, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class CanvasPositionRepository:
    def to_domain(self, row: CanvasPosition) -> CanvasPositionRecord:
        return CanvasPositionRecord(
            plan_id=row.plan_id,
            node_id=row.node_id,
            x=row.x,
            y=row.y,
            user_moved=row.user_moved,
            updated_at=row.updated_at,
        )

    def from_domain(self, record: CanvasPositionRecord) -> CanvasPosition:
        return CanvasPosition(
            plan_id=record.plan_id,
            node_id=record.node_id,
            x=record.x,
            y=record.y,
            user_moved=record.user_moved,
            updated_at=record.updated_at,
        )

    def identity(self, record: CanvasPositionRecord) -> tuple[object, ...]:
        return (record.plan_id, record.node_id)

    def add(self, session: Session, record: CanvasPositionRecord) -> CanvasPositionRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> CanvasPositionRecord:
        found = session.get(CanvasPosition, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class KbNodeRepository:
    def to_domain(self, row: KbNode) -> KbNodeRecord:
        return KbNodeRecord(
            id=row.id,
            project_id=row.project_id,
            node_type=row.node_type,
            title=row.title,
            note_ref=row.note_ref,
            status=row.status,
            source=row.source,
            verified_commit=row.verified_commit,
            is_stale=row.is_stale,
            updated_at=row.updated_at,
        )

    def from_domain(self, record: KbNodeRecord) -> KbNode:
        return KbNode(
            id=record.id,
            project_id=record.project_id,
            node_type=record.node_type,
            title=record.title,
            note_ref=record.note_ref,
            status=record.status,
            source=record.source,
            verified_commit=record.verified_commit,
            is_stale=record.is_stale,
            updated_at=record.updated_at,
        )

    def identity(self, record: KbNodeRecord) -> object:
        return record.id

    def add(self, session: Session, record: KbNodeRecord) -> KbNodeRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> KbNodeRecord:
        found = session.get(KbNode, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class KbEdgeRepository:
    def to_domain(self, row: KbEdge) -> KbEdgeRecord:
        return KbEdgeRecord(
            src_id=row.src_id,
            dst_id=row.dst_id,
            kind=row.kind,
            origin=row.origin,
        )

    def from_domain(self, record: KbEdgeRecord) -> KbEdge:
        return KbEdge(
            src_id=record.src_id,
            dst_id=record.dst_id,
            kind=record.kind,
            origin=record.origin,
        )

    def identity(self, record: KbEdgeRecord) -> tuple[object, ...]:
        return (record.src_id, record.dst_id, record.kind)

    def add(self, session: Session, record: KbEdgeRecord) -> KbEdgeRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> KbEdgeRecord:
        found = session.get(KbEdge, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class VaultFileRepository:
    def to_domain(self, row: VaultFile) -> VaultFileRecord:
        return VaultFileRecord(
            note_ref=row.note_ref,
            kind=row.kind,
            owner=row.owner,
            kb_id=row.kb_id,
            plan_node_id=row.plan_node_id,
            frontmatter_hash=row.frontmatter_hash,
            body_hash=row.body_hash,
            base_frontmatter=row.base_frontmatter,
            mtime_ns=row.mtime_ns,
            size=row.size,
            inode=row.inode,
            last_writer=row.last_writer,
            pending_write=row.pending_write,
            synced_at=row.synced_at,
        )

    def from_domain(self, record: VaultFileRecord) -> VaultFile:
        return VaultFile(
            note_ref=record.note_ref,
            kind=record.kind,
            owner=record.owner,
            kb_id=record.kb_id,
            plan_node_id=record.plan_node_id,
            frontmatter_hash=record.frontmatter_hash,
            body_hash=record.body_hash,
            base_frontmatter=record.base_frontmatter,
            mtime_ns=record.mtime_ns,
            size=record.size,
            inode=record.inode,
            last_writer=record.last_writer,
            pending_write=record.pending_write,
            synced_at=record.synced_at,
        )

    def identity(self, record: VaultFileRecord) -> object:
        return record.note_ref

    def add(self, session: Session, record: VaultFileRecord) -> VaultFileRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> VaultFileRecord:
        found = session.get(VaultFile, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class HandoverRepository:
    def to_domain(self, row: Handover) -> HandoverRecord:
        return HandoverRecord(
            id=row.id,
            project_id=row.project_id,
            source_tab_id=row.source_tab_id,
            target_agent=row.target_agent,
            plan_node_id=row.plan_node_id,
            summary_markdown=row.summary_markdown,
            note_ref=row.note_ref,
            created_at=row.created_at,
        )

    def from_domain(self, record: HandoverRecord) -> Handover:
        return Handover(
            id=record.id,
            project_id=record.project_id,
            source_tab_id=record.source_tab_id,
            target_agent=record.target_agent,
            plan_node_id=record.plan_node_id,
            summary_markdown=record.summary_markdown,
            note_ref=record.note_ref,
            created_at=record.created_at,
        )

    def identity(self, record: HandoverRecord) -> object:
        return record.id

    def add(self, session: Session, record: HandoverRecord) -> HandoverRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> HandoverRecord:
        found = session.get(Handover, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class IterationRepository:
    def to_domain(self, row: Iteration) -> IterationRecord:
        return IterationRecord(
            project_id=row.project_id,
            number=row.number,
            dag_state_hash=row.dag_state_hash,
            manifest_note_ref=row.manifest_note_ref,
            type_check_passed=row.type_check_passed,
            created_at=row.created_at,
        )

    def from_domain(self, record: IterationRecord) -> Iteration:
        return Iteration(
            project_id=record.project_id,
            number=record.number,
            dag_state_hash=record.dag_state_hash,
            manifest_note_ref=record.manifest_note_ref,
            type_check_passed=record.type_check_passed,
            created_at=record.created_at,
        )

    def identity(self, record: IterationRecord) -> tuple[object, ...]:
        return (record.project_id, record.number)

    def add(self, session: Session, record: IterationRecord) -> IterationRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> IterationRecord:
        found = session.get(Iteration, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class FrecencyRepository:
    def to_domain(self, row: Frecency) -> FrecencyRecord:
        return FrecencyRecord(
            item_id=row.item_id,
            score=row.score,
            last_used=row.last_used,
            uses=row.uses,
        )

    def from_domain(self, record: FrecencyRecord) -> Frecency:
        return Frecency(
            item_id=record.item_id,
            score=record.score,
            last_used=record.last_used,
            uses=record.uses,
        )

    def identity(self, record: FrecencyRecord) -> object:
        return record.item_id

    def add(self, session: Session, record: FrecencyRecord) -> FrecencyRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> FrecencyRecord:
        found = session.get(Frecency, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


class SettingRepository:
    def to_domain(self, row: Setting) -> SettingRecord:
        return SettingRecord(
            key=row.key,
            value=row.value,
            rev=row.rev,
        )

    def from_domain(self, record: SettingRecord) -> Setting:
        return Setting(
            key=record.key,
            value=record.value,
            rev=record.rev,
        )

    def identity(self, record: SettingRecord) -> object:
        return record.key

    def add(self, session: Session, record: SettingRecord) -> SettingRecord:
        row = self.from_domain(record)
        _persist(session, row)
        return self.to_domain(row)

    def one(self, session: Session, key: object) -> SettingRecord:
        found = session.get(Setting, key)
        if found is None:
            message = "row is missing"
            raise KeyError(message)
        return self.to_domain(found)


REPOSITORIES = (
    ProjectRepository(),
    AgentAccountRepository(),
    WorkspaceRepository(),
    PlanRepository(),
    PlanNodeRepository(),
    TabRepository(),
    AgentSessionRepository(),
    EventLogRepository(),
    QuotaCurrentRepository(),
    QuotaHistoryRepository(),
    ApprovalRepository(),
    ApprovalRuleRepository(),
    CheckpointRepository(),
    PlanEdgeRepository(),
    LeaseRepository(),
    NodeArtifactRepository(),
    CanvasPositionRepository(),
    KbNodeRepository(),
    KbEdgeRepository(),
    VaultFileRepository(),
    HandoverRepository(),
    IterationRepository(),
    FrecencyRepository(),
    SettingRepository(),
)
