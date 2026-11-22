"""ORM models from DATA_MODEL §3. These are not the domain dataclasses."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import ClassVar, final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    column,
    false,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from aconsole.core.domain.agent import AgentKind
from aconsole.core.domain.plan import NodeStatus
from aconsole.core.domain.tab_status import TabStatus
from aconsole.core.domain.workspace import WorkspaceMode
from aconsole.core.infrastructure.persistence.types import JsonList, JsonObject, UtcDateTime

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class EventSource(StrEnum):
    CLAUDE_HOOK = "claude_hook"
    CLAUDE_STATUSLINE = "claude_statusline"
    CODEX_HOOK = "codex_hook"
    CODEX_NOTIFY = "codex_notify"
    SIDECAR = "sidecar"
    CORE = "core"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    ALLOWED = "allowed"
    DENIED = "denied"
    EXPIRED_TO_NATIVE = "expired_to_native"
    CANCELLED = "cancelled"


class PlanStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class EdgeKind(StrEnum):
    BLOCKS = "blocks"
    PARENT = "parent"
    RELATED = "related"


class ArtifactKind(StrEnum):
    COMMIT = "commit"
    FILE = "file"
    TEST_REPORT = "test_report"
    URL = "url"
    NOTE = "note"


class KbType(StrEnum):
    FACT = "fact"
    DECISION = "decision"
    REQUIREMENT = "requirement"
    MODULE = "module"
    UNRESOLVED = "unresolved"


class KbEdgeKind(StrEnum):
    IMPORTS = "imports"
    USES_TYPE = "uses_type"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    PROJECT_REF = "project_ref"
    CODE_REF = "code_ref"
    DEPENDS_ON = "depends_on"
    SUPERSEDES = "supersedes"
    LINKS = "links"


def _values(members: type[StrEnum]) -> list[str]:
    return [member.value for member in members]


def _table_args(*parts: object) -> tuple[object, ...]:
    return parts


def _enum(kind: type[StrEnum]) -> Enum:
    return Enum(kind, native_enum=False, validate_strings=True, length=24, values_callable=_values)


class Base(DeclarativeBase):
    metadata: ClassVar[MetaData] = MetaData(naming_convention=NAMING)
    type_annotation_map: ClassVar[dict[type[object], type]] = {
        datetime: UtcDateTime,
        list[str]: JsonList,
        dict[str, object]: JsonObject,
    }


@final
class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    root_path: Mapped[str] = mapped_column(String(4096), unique=True)
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    vault_folder: Mapped[str | None] = mapped_column(String(1024))
    languages: Mapped[list[str]] = mapped_column(JsonList)
    created_at: Mapped[datetime] = mapped_column()
    # Null inherits the global approval policy stored in the settings document.
    approval_policy: Mapped[str | None] = mapped_column(String(16), nullable=True)
    approval_dangerous: Mapped[str | None] = mapped_column(String(16), nullable=True)


@final
class ProjectAnalysisTrust(Base):
    """A missing row means the project is untrusted.

    Kept off ``projects`` so a downgrade to an older revision can still insert a project
    with the current ``Project`` model.
    """

    __tablename__ = "project_analysis_trust"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    analysis_trusted: Mapped[bool] = mapped_column(
        Boolean(create_constraint=True),
        nullable=False,
        default=False,
        server_default=false(),
    )


@final
class AgentAccount(Base):
    __tablename__ = "agent_accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_kind: Mapped[AgentKind] = mapped_column(_enum(AgentKind))
    plan_label: Mapped[str] = mapped_column(String(32))
    container_name: Mapped[str] = mapped_column(String(128))


@final
class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    mode: Mapped[WorkspaceMode] = mapped_column(_enum(WorkspaceMode))
    path: Mapped[str] = mapped_column(String(4096), unique=True)
    branch: Mapped[str | None] = mapped_column(String(255))
    base_branch: Mapped[str | None] = mapped_column(String(255))
    base_commit: Mapped[str | None] = mapped_column(String(64))
    locked: Mapped[bool] = mapped_column(Boolean(create_constraint=True))
    dirty_cached: Mapped[bool] = mapped_column(Boolean(create_constraint=True))
    created_at: Mapped[datetime] = mapped_column()
    removed_at: Mapped[datetime | None] = mapped_column()


@final
class WorkSessionRow(Base):
    __tablename__ = "work_sessions"
    __table_args__ = _table_args(
        Index(
            "ix_work_sessions_workspace",
            "worktree",
            unique=True,
            sqlite_where=column("workspace_removed") == false(),
        )
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    worktree: Mapped[str] = mapped_column(String(4096))
    workspace_removed: Mapped[bool] = mapped_column(default=False)
    branch: Mapped[str | None] = mapped_column(String(255))
    base_branch: Mapped[str | None] = mapped_column(String(255))
    workspace_mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column()


@final
class WorkSessionSnapshotRow(Base):
    __tablename__ = "work_session_snapshots"

    work_session_id: Mapped[str] = mapped_column(
        ForeignKey("work_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    sequence: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    actor_tab_id: Mapped[str | None] = mapped_column(String(32))
    actor_title: Mapped[str | None] = mapped_column(String(80))
    agent: Mapped[str | None] = mapped_column(String(32))
    plan_node: Mapped[str | None] = mapped_column(String(64))
    worktree: Mapped[str] = mapped_column(String(4096))
    ref: Mapped[str] = mapped_column(String(255))
    head_oid: Mapped[str | None] = mapped_column(String(64))
    oid: Mapped[str | None] = mapped_column(String(64))
    tree: Mapped[str | None] = mapped_column(String(64))
    previous_oid: Mapped[str | None] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column()


@final
class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = _table_args(UniqueConstraint("project_id", "title"))

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[PlanStatus] = mapped_column(_enum(PlanStatus))
    phase: Mapped[str] = mapped_column(
        String(16),
        CheckConstraint("phase IN ('draft', 'frozen', 'completed')", name="phase"),
        default="draft",
        server_default="draft",
    )
    revision_no: Mapped[int] = mapped_column(default=1, server_default="1")
    frozen_at: Mapped[datetime | None] = mapped_column()
    baseline_nodes: Mapped[int] = mapped_column(default=0, server_default="0")
    gross_added: Mapped[int] = mapped_column(default=0, server_default="0")
    gates_sha256: Mapped[str | None] = mapped_column(String(64))
    canvas_note_ref: Mapped[str | None] = mapped_column(String(1024))
    created_by_tab: Mapped[str | None] = mapped_column(
        ForeignKey("tabs.id", ondelete="SET NULL", use_alter=True, name="fk_plans_created_by_tab_tabs")
    )
    created_at: Mapped[datetime] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column()


@final
class PlanNode(Base):
    __tablename__ = "plan_nodes"
    __table_args__ = _table_args(
        UniqueConstraint("plan_id", "node_key"),
        Index("ix_nodes_plan_status", "plan_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"))
    node_key: Mapped[str] = mapped_column(String(12))
    title: Mapped[str] = mapped_column(String(120))
    objective: Mapped[str] = mapped_column(Text)
    status: Mapped[NodeStatus] = mapped_column(_enum(NodeStatus))
    owner_agent: Mapped[AgentKind | None] = mapped_column(_enum(AgentKind))
    paths: Mapped[list[str]] = mapped_column(JsonList)
    acceptance: Mapped[list[str]] = mapped_column(JsonList)
    test_cmd: Mapped[str | None] = mapped_column(String(1000))
    review_required: Mapped[bool] = mapped_column(Boolean(create_constraint=True))
    evidence: Mapped[str | None] = mapped_column(Text)
    note_ref: Mapped[str | None] = mapped_column(String(1024))
    layer: Mapped[int] = mapped_column()
    priority: Mapped[float] = mapped_column(Float)
    unmet_blockers: Mapped[int] = mapped_column()
    fence_seq: Mapped[int] = mapped_column()
    created_at: Mapped[datetime] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column()
    level: Mapped[int] = mapped_column(CheckConstraint("level IN (0, 1)", name="level"), default=1, server_default="1")
    pipeline: Mapped[bool] = mapped_column(default=False, server_default=false())
    max_attempts: Mapped[int] = mapped_column(
        CheckConstraint("max_attempts BETWEEN 1 AND 4", name="max_attempts"),
        default=4,
        server_default="4",
    )
    contract_sha256: Mapped[str | None] = mapped_column(String(64))
    invariants: Mapped[str | None] = mapped_column(Text)
    gate_profile: Mapped[dict[str, object] | None] = mapped_column(JsonObject)
    blast_radius: Mapped[list[str] | None] = mapped_column(JsonList)
    baseline_children: Mapped[int] = mapped_column(default=0, server_default="0")
    boundaries: Mapped[str | None] = mapped_column(Text)
    output_format: Mapped[str | None] = mapped_column(Text)
    agent_note: Mapped[str | None] = mapped_column(Text)


@final
class Tab(Base):
    __tablename__ = "tabs"
    __table_args__ = _table_args(
        Index("ix_tabs_project_order", "project_id", "tab_order"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    work_session_id: Mapped[str | None] = mapped_column(String(32), index=True)
    isolated: Mapped[bool] = mapped_column(default=False)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id", ondelete="SET NULL"))
    agent_kind: Mapped[AgentKind] = mapped_column(_enum(AgentKind))
    mode: Mapped[str] = mapped_column(
        String(16),
        CheckConstraint("mode IN ('plan', 'execute')", name="mode"),
        default="execute",
        server_default="execute",
    )
    model: Mapped[str | None] = mapped_column(String(64))
    account_id: Mapped[str | None] = mapped_column(ForeignKey("agent_accounts.id"))
    title: Mapped[str] = mapped_column(String(80))
    tab_order: Mapped[int] = mapped_column()
    last_status: Mapped[TabStatus] = mapped_column(_enum(TabStatus))
    status_detail: Mapped[str | None] = mapped_column(String(200))
    plan_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("plan_nodes.id", ondelete="SET NULL", use_alter=True, name="fk_tabs_plan_node_id_plan_nodes")
    )
    context_used_pct: Mapped[float | None] = mapped_column(Float)
    session_cost_usd: Mapped[float | None] = mapped_column(Float)
    agent_session_id: Mapped[str | None] = mapped_column(String(64))
    fork_pending: Mapped[bool] = mapped_column(default=False, server_default="0")
    closed_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column()


@final
class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    tab_id: Mapped[str] = mapped_column(ForeignKey("tabs.id", ondelete="CASCADE"), index=True)
    agent_session_id: Mapped[str] = mapped_column(String(64), index=True)
    transcript_path: Mapped[str | None] = mapped_column(String(4096))
    started_at: Mapped[datetime] = mapped_column()
    ended_at: Mapped[datetime | None] = mapped_column()


@final
class EventLog(Base):
    __tablename__ = "events"
    __table_args__ = _table_args(Index("ix_events_tab_id", "tab_id", "id"))

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tab_id: Mapped[str] = mapped_column(ForeignKey("tabs.id", ondelete="CASCADE"))
    source: Mapped[EventSource] = mapped_column(_enum(EventSource))
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JsonObject)
    at: Mapped[datetime] = mapped_column()


@final
class QuotaCurrent(Base):
    __tablename__ = "quota_current"

    account_id: Mapped[str] = mapped_column(ForeignKey("agent_accounts.id", ondelete="CASCADE"), primary_key=True)
    window_key: Mapped[str] = mapped_column(String(8), primary_key=True)
    used_pct: Mapped[float | None] = mapped_column(Float)
    resets_at: Mapped[datetime | None] = mapped_column()
    estimated: Mapped[bool] = mapped_column(Boolean(create_constraint=True))
    source: Mapped[str] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column()


@final
class QuotaHistory(Base):
    __tablename__ = "quota_history"
    __table_args__ = _table_args(
        Index("ix_quota_hist", "account_id", "window_key", "observed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(64))
    window_key: Mapped[str] = mapped_column(String(8))
    used_pct: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column()


@final
class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    tab_id: Mapped[str] = mapped_column(ForeignKey("tabs.id", ondelete="CASCADE"), index=True)
    agent_session_id: Mapped[str] = mapped_column(String(64))
    tool: Mapped[str] = mapped_column(String(128))
    input_sha256: Mapped[str] = mapped_column(String(64))
    nonce: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(String(300))
    risk: Mapped[list[str]] = mapped_column(JsonList)
    status: Mapped[ApprovalStatus] = mapped_column(_enum(ApprovalStatus))
    decided_by: Mapped[str | None] = mapped_column(String(16))
    requested_at: Mapped[datetime] = mapped_column()
    decided_at: Mapped[datetime | None] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column()


@final
class ApprovalRule(Base):
    __tablename__ = "approval_rules"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    tab_id: Mapped[str] = mapped_column(ForeignKey("tabs.id", ondelete="CASCADE"), index=True)
    tool: Mapped[str] = mapped_column(String(128))
    command_prefix: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column()


@final
class Checkpoint(Base):
    __tablename__ = "checkpoints"
    __table_args__ = _table_args(UniqueConstraint("tab_id", "turn"))

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    tab_id: Mapped[str] = mapped_column(ForeignKey("tabs.id", ondelete="CASCADE"))
    turn: Mapped[int] = mapped_column()
    git_ref: Mapped[str] = mapped_column(String(255))
    tree_sha: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(200))
    files_changed: Mapped[int] = mapped_column()
    insertions: Mapped[int] = mapped_column()
    deletions: Mapped[int] = mapped_column()
    created_at: Mapped[datetime] = mapped_column()


@final
class GitOperationRow(Base):
    """Outlives its project and tab on purpose: it says what happened to a branch. Nothing here cascades."""

    __tablename__ = "git_operations"
    __table_args__ = _table_args(Index("ix_git_operations_open", "status"))

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64))
    work_session_id: Mapped[str | None] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24))
    mode: Mapped[str | None] = mapped_column(String(24))
    source: Mapped[str] = mapped_column(String(512))
    target: Mapped[str] = mapped_column(String(512))
    source_oid: Mapped[str] = mapped_column(String(64))
    target_oid: Mapped[str] = mapped_column(String(64))
    result_oid: Mapped[str | None] = mapped_column(String(64))
    expected_tree: Mapped[str | None] = mapped_column(String(64))
    worktree: Mapped[str | None] = mapped_column(Text)
    tab_id: Mapped[str | None] = mapped_column(String(64))
    plan_node: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str | None] = mapped_column(Text)
    follow_up_pending: Mapped[bool] = mapped_column()
    created_at: Mapped[datetime] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column()


@final
class PlanEdge(Base):
    __tablename__ = "plan_edges"
    __table_args__ = _table_args(Index("ix_edges_dst", "dst_node_id", "kind"))

    src_node_id: Mapped[str] = mapped_column(ForeignKey("plan_nodes.id", ondelete="CASCADE"), primary_key=True)
    dst_node_id: Mapped[str] = mapped_column(ForeignKey("plan_nodes.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[EdgeKind] = mapped_column(_enum(EdgeKind), primary_key=True)


@final
class Lease(Base):
    __tablename__ = "leases"

    node_id: Mapped[str] = mapped_column(ForeignKey("plan_nodes.id", ondelete="CASCADE"), primary_key=True)
    lease_id: Mapped[str] = mapped_column(String(26), unique=True)
    tab_id: Mapped[str] = mapped_column(ForeignKey("tabs.id", ondelete="CASCADE"))
    fence: Mapped[int] = mapped_column()
    acquired_at: Mapped[datetime] = mapped_column()
    renewed_at: Mapped[datetime] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column()


@final
class NodeArtifact(Base):
    __tablename__ = "node_artifacts"
    __table_args__ = _table_args(UniqueConstraint("node_id", "kind", "ref"))

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("plan_nodes.id", ondelete="CASCADE"), index=True)
    kind: Mapped[ArtifactKind] = mapped_column(_enum(ArtifactKind))
    ref: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column()


@final
class CanvasPosition(Base):
    __tablename__ = "canvas_positions"

    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("plan_nodes.id", ondelete="CASCADE"), primary_key=True)
    x: Mapped[int] = mapped_column()
    y: Mapped[int] = mapped_column()
    user_moved: Mapped[bool] = mapped_column(Boolean(create_constraint=True))
    updated_at: Mapped[datetime] = mapped_column()


@final
class KbNode(Base):
    __tablename__ = "kb_nodes"
    __table_args__ = _table_args(
        Index("ix_kb_nodes_project_type", "project_id", "node_type"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    node_type: Mapped[KbType] = mapped_column(_enum(KbType))
    title: Mapped[str] = mapped_column(String(300))
    note_ref: Mapped[str | None] = mapped_column(String(1024), unique=True)
    status: Mapped[str | None] = mapped_column(String(24))
    source: Mapped[str | None] = mapped_column(String(2048))
    verified_commit: Mapped[str | None] = mapped_column(String(64))
    body: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(16))
    body_hash: Mapped[str | None] = mapped_column(String(64))
    interface_hash: Mapped[str | None] = mapped_column(String(64))
    is_stale: Mapped[bool] = mapped_column(Boolean(create_constraint=True))
    updated_at: Mapped[datetime] = mapped_column()


@final
class KbEdge(Base):
    __tablename__ = "kb_edges"
    __table_args__ = _table_args(
        Index("ix_kb_edges_dst", "dst_id", "src_id", "kind"),
    )

    src_id: Mapped[str] = mapped_column(ForeignKey("kb_nodes.id", ondelete="CASCADE"), primary_key=True)
    dst_id: Mapped[str] = mapped_column(ForeignKey("kb_nodes.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[KbEdgeKind] = mapped_column(_enum(KbEdgeKind), primary_key=True)
    origin: Mapped[str] = mapped_column(String(16))


@final
class VaultFile(Base):
    __tablename__ = "vault_files"

    note_ref: Mapped[str] = mapped_column(String(1024), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    owner: Mapped[str] = mapped_column(String(8))
    kb_id: Mapped[str | None] = mapped_column(String(64))
    plan_node_id: Mapped[str | None] = mapped_column(String(26))
    frontmatter_hash: Mapped[str | None] = mapped_column(String(64))
    body_hash: Mapped[str | None] = mapped_column(String(64))
    base_frontmatter: Mapped[dict[str, object] | None] = mapped_column(JsonObject)
    mtime_ns: Mapped[int | None] = mapped_column()
    size: Mapped[int | None] = mapped_column()
    inode: Mapped[int | None] = mapped_column()
    last_writer: Mapped[str] = mapped_column(String(8))
    pending_write: Mapped[dict[str, object] | None] = mapped_column(JsonObject)
    synced_at: Mapped[datetime] = mapped_column()


@final
class Handover(Base):
    __tablename__ = "handovers"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    source_tab_id: Mapped[str | None] = mapped_column(ForeignKey("tabs.id", ondelete="SET NULL"))
    target_agent: Mapped[AgentKind | None] = mapped_column(_enum(AgentKind))
    plan_node_id: Mapped[str | None] = mapped_column(ForeignKey("plan_nodes.id", ondelete="SET NULL"))
    summary_markdown: Mapped[str] = mapped_column(Text)
    packet: Mapped[dict[str, object] | None] = mapped_column(JsonObject)
    note_ref: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column()


@final
class Iteration(Base):
    __tablename__ = "iterations"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    number: Mapped[int] = mapped_column(primary_key=True)
    dag_state_hash: Mapped[str] = mapped_column(String(64))
    manifest_note_ref: Mapped[str] = mapped_column(String(1024))
    type_check_passed: Mapped[bool] = mapped_column(Boolean(create_constraint=True))
    created_at: Mapped[datetime] = mapped_column()


@final
class Frecency(Base):
    __tablename__ = "frecency"

    item_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    score: Mapped[float] = mapped_column(Float)
    last_used: Mapped[datetime] = mapped_column()
    uses: Mapped[int] = mapped_column()


@final
class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, object]] = mapped_column(JsonObject)
    rev: Mapped[int] = mapped_column()


@final
class GitRefDeletion(Base):
    """Outlives deleted checkpoint/tab rows until the Git deletion is acknowledged."""

    __tablename__ = "git_ref_deletions"

    repo_path: Mapped[str] = mapped_column(String(4096), primary_key=True)
    git_ref: Mapped[str] = mapped_column(String(255), primary_key=True)


@final
class PlanInbox(Base):
    __tablename__ = "plan_inbox"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str | None] = mapped_column(ForeignKey("plan_nodes.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(
        String(16),
        CheckConstraint("kind IN ('backlog','replan','human','add','split','reopen')", name="inbox_kind"),
    )
    body: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, object]] = mapped_column(JsonObject, default=dict)
    tab_id: Mapped[str | None] = mapped_column(ForeignKey("tabs.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(
        String(16),
        CheckConstraint("status IN ('open', 'applied', 'rejected')", name="inbox_status"),
        default="open",
        server_default="open",
    )
    created_at: Mapped[datetime] = mapped_column()
    decided_at: Mapped[datetime | None] = mapped_column()
    decided_by: Mapped[str | None] = mapped_column(String(64))


@final
class NodeAttempt(Base):
    __tablename__ = "node_attempts"
    __table_args__ = _table_args(
        UniqueConstraint("node_id", "attempt_no"),
        CheckConstraint("attempt_no BETWEEN 1 AND 4", name="attempt_no"),
        CheckConstraint("status IN ('running','passed','failed','abandoned')", name="status"),
        CheckConstraint("length(compact_verdict) <= 500", name="compact_verdict"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("plan_nodes.id", ondelete="CASCADE"))
    attempt_no: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(24))
    tab_id: Mapped[str | None] = mapped_column(String(26))
    agent_kind: Mapped[str | None] = mapped_column(String(24))
    model: Mapped[str | None] = mapped_column(String(64))
    base_oid: Mapped[str] = mapped_column(String(64))
    head_block: Mapped[str | None] = mapped_column(String(64))
    diff_stat: Mapped[str | None] = mapped_column(String(200))
    compact_verdict: Mapped[str | None] = mapped_column(String(500))
    cost_usd: Mapped[float | None] = mapped_column(Float)
    started_at: Mapped[datetime] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()


@final
class NodeStepBlock(Base):
    __tablename__ = "node_step_blocks"
    __table_args__ = _table_args(
        UniqueConstraint("attempt_id", "seq"),
        CheckConstraint("seq BETWEEN 1 AND 4", name="seq"),
        CheckConstraint("step_type IN ('spec','patch','test','verify')", name="step_type"),
    )

    block_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("node_attempts.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column()
    step_type: Mapped[str] = mapped_column(String(8))
    prev_block_hash: Mapped[str] = mapped_column(String(64))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column()


@final
class GateReceipt(Base):
    __tablename__ = "gate_receipts"
    __table_args__ = _table_args(
        Index("ix_gate_receipts_node", "node_id", "tier", "created_at"),
        CheckConstraint("tier IN ('micro','blast','direction')", name="tier"),
        CheckConstraint("length(compact_verdict) <= 500", name="compact_verdict"),
        CheckConstraint("duration_ms >= 0", name="duration_ms"),
    )

    receipt_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("plan_nodes.id", ondelete="CASCADE"))
    tier: Mapped[str] = mapped_column(String(12))
    state_hash: Mapped[str] = mapped_column(String(64))
    argv_sha256: Mapped[str] = mapped_column(String(64))
    exit_code: Mapped[int] = mapped_column()
    passed: Mapped[bool] = mapped_column(Boolean(create_constraint=True))
    compact_verdict: Mapped[str] = mapped_column(String(500))
    log_ref: Mapped[str | None] = mapped_column(String(1024))
    duration_ms: Mapped[int] = mapped_column()
    created_at: Mapped[datetime] = mapped_column()
