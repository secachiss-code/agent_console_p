"""ORM models from DATA_MODEL §3. These are not the domain dataclasses."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import ClassVar, final

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Index, MetaData, String, Text, UniqueConstraint
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
class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = _table_args(UniqueConstraint("project_id", "title"))

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[PlanStatus] = mapped_column(_enum(PlanStatus))
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


@final
class Tab(Base):
    __tablename__ = "tabs"
    __table_args__ = _table_args(
        Index("ix_tabs_project_order", "project_id", "tab_order"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id", ondelete="SET NULL"))
    agent_kind: Mapped[AgentKind] = mapped_column(_enum(AgentKind))
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
