"""One-effect methods shared by the local REST API and the agent tools.

Each call names its project, plan, or node. A tab id is present only when the
effect belongs to a lease. MCP fills those ids from the tab token and may pack
several of these results into one tool answer.
"""

from typing import Annotated, Literal

import msgspec
from msgspec import Meta

from aconsole.contracts.app import ApprovalView, PlanNodeSpec, ProjectView
from aconsole.contracts.common import NodeId, PlanId, ProjectId, TabId

_Text = Annotated[str, Meta(max_length=2000)]
_Items = Annotated[tuple[_Text, ...], Meta(max_length=100)]


class NodeKey(msgspec.Struct, frozen=True, kw_only=True):
    key: str
    node_id: str


class PlanCreateParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    title: Annotated[str, Meta(min_length=1, max_length=120)]
    goal: Annotated[str, Meta(max_length=8000)] = ""


class PlanCreateResult(msgspec.Struct, frozen=True, kw_only=True):
    plan_id: str
    nodes: tuple[NodeKey, ...]
    canvas_note: str


class PlanAddNodeParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    plan_id: PlanId
    node: PlanNodeSpec


class PlanAddNodeResult(msgspec.Struct, frozen=True, kw_only=True):
    added: tuple[NodeKey, ...]
    unchanged: tuple[str, ...]


class PlanReadyParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    plan_id: PlanId | None = None
    limit: Annotated[int, Meta(ge=1, le=20)] = 5


class ReadyNode(msgspec.Struct, frozen=True, kw_only=True):
    node_id: str
    key: str
    title: str
    priority: float
    paths: tuple[str, ...]
    owner_agent: str | None


class PlanReadyResult(msgspec.Struct, frozen=True, kw_only=True):
    nodes: tuple[ReadyNode, ...]


class NodeClaimParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    node_id: NodeId
    tab_id: TabId


class NodeBriefText(msgspec.Struct, frozen=True, kw_only=True):
    objective: str
    acceptance: tuple[str, ...]
    paths: tuple[str, ...]
    boundaries: str | None
    output_format: str | None


class KnowledgeItem(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    type: str
    title: str
    summary: str
    source: str
    note_ref: str
    stale: bool


class KnowledgeGetResult(msgspec.Struct, frozen=True, kw_only=True):
    items: tuple[KnowledgeItem, ...]
    truncated: bool
    verified_commit: str | None = None


class NodeClaimResult(msgspec.Struct, frozen=True, kw_only=True):
    lease_id: str
    fence: int
    expires_at: str
    brief: NodeBriefText
    context: KnowledgeGetResult


class NodeUpdateParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    node_id: NodeId
    tab_id: TabId
    fence: int | None = None
    status: Annotated[str, Meta(max_length=32)] | None = None
    note: Annotated[str, Meta(max_length=2000)] | None = None
    evidence: Annotated[str, Meta(max_length=4000)] | None = None


class NodeUpdateResult(msgspec.Struct, frozen=True, kw_only=True):
    status: str
    expires_at: str | None


class NodeAttachParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    node_id: NodeId
    tab_id: TabId
    fence: int
    kind: Literal["commit", "file", "test_report", "url", "note"]
    ref: _Text
    title: Annotated[str, Meta(max_length=300)] | None = None


class NodeAttachResult(msgspec.Struct, frozen=True, kw_only=True):
    artifact_id: str


class NodeStepParams(msgspec.Struct, frozen=True, kw_only=True):
    """Record one L2 step, or restore the scope when ``step`` is ``reset``."""

    project_id: ProjectId
    node_id: NodeId
    tab_id: TabId
    fence: int
    step: Literal["spec", "patch", "test", "verify", "reset"]
    affected_symbols: Annotated[tuple[str, ...], Meta(max_length=12)] = ()
    note: _Text | None = None
    patch: Annotated[str, Meta(max_length=256 * 1024)] | None = None


class NodeEscalateParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    node_id: NodeId
    tab_id: TabId
    fence: int
    reason: Annotated[str, Meta(min_length=1, max_length=2000)]
    kind: Annotated[str, Meta(min_length=1, max_length=32)] = "replan"


class GateRunParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    node_id: NodeId
    tab_id: TabId
    fence: int
    tier: Literal["micro", "blast"]


class PipelineStepResult(msgspec.Struct, frozen=True, kw_only=True):
    """The pipeline body's JSON. One step or one gate, not a packed tool answer."""

    reply_json: str


class KnowledgeGetParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    node_id: NodeId | None = None
    query: _Text | None = None
    max_items: Annotated[int, Meta(ge=1, le=8)] = 6


class KnowledgeAddParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    kind: Literal["fact", "decision", "requirement"]
    title: Annotated[str, Meta(max_length=300)]
    body: Annotated[str, Meta(max_length=8000)]
    source: _Text
    code_refs: _Items = ()
    supersedes: str | None = None


class KnowledgeAddResult(msgspec.Struct, frozen=True, kw_only=True):
    kb_id: str
    note_ref: str


class HandoverReadParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    tab_id: TabId


class HandoverWriteParams(msgspec.Struct, frozen=True, kw_only=True):
    project_id: ProjectId
    tab_id: TabId
    goal: Annotated[str, Meta(max_length=8000)] | None = None
    done: _Items = ()
    decisions: _Items = ()
    open_questions: _Items = ()
    next_steps: _Items = ()
    failing_checks: _Items = ()


class HandoverView(msgspec.Struct, frozen=True, kw_only=True):
    handover_id: str | None = None
    note_ref: str | None = None
    goal: str = ""
    done: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    failing_checks: tuple[str, ...] = ()
    diff_stat: str = ""


class ProjectListResult(msgspec.Struct, frozen=True, kw_only=True):
    projects: tuple[ProjectView, ...]


class ApprovalListResult(msgspec.Struct, frozen=True, kw_only=True):
    approvals: tuple[ApprovalView, ...]
