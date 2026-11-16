"""The twelve MCP plan tools, backed by the domain plan DAG.

A repeated ``plan_create`` for the same project and title returns the same plan.
Active-lease ``node_update`` and ``node_attach`` require the fence from ``node_claim``.
An unleased transition may omit the ``node_update`` fence.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, ClassVar, Literal, NoReturn, Protocol
from uuid import uuid4

import msgspec
import pydantic_core
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, Field, RootModel, TypeAdapter

from aconsole.contracts.app import PlanNodeSpec, PlanNodeView, PlanView
from aconsole.contracts.common import AgentKind, NodeId, NoteRef, PlanId, ProjectId, TabId
from aconsole.contracts.common import NodeStatus as NodeStatusView
from aconsole.core.application.ports.gate_runner import GateRunnerUnavailableError
from aconsole.core.application.ports.git_repo import GitCommandError
from aconsole.core.application.ports.knowledge_store import KnowledgeStore, StoredFact, StoredHandover
from aconsole.core.application.ports.plan_store import (
    PlanChangeStore,
    PlanInboxStore,
    PlanStore,
    StoredArtifact,
    StoredNode,
    StoredPlan,
    StoredPlanChange,
    StoredPlanDecision,
    StoreWriteError,
)
from aconsole.core.application.services.context_shaper import DirectionContext, LeaseTail, ModuleContext
from aconsole.core.application.services.gates import DirectionVerifier
from aconsole.core.application.services.pipeline import PipelineActor, PipelineContract, PipelineService
from aconsole.core.application.services.plan_lifecycle import (
    apply_plan_change,
    freeze_plan,
    require_note_contract,
    topology_contract,
    topology_for,
)
from aconsole.core.application.services.plan_review import review_move
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.gate_profile import micro_commands
from aconsole.core.domain.pipeline import Step
from aconsole.core.domain.plan import Move, Node, NodeSpec, NodeStatus, Plan, Role, after_merge
from aconsole.core.domain.plan_admission import PlanTopology
from aconsole.core.domain.scope import check_scope
from aconsole.core.domain.vault_sync import Note as VaultNote
from aconsole.core.domain.vault_sync import Owners, resolve_ref
from aconsole.core.infrastructure.git.diff import GitDiff
from aconsole.core.infrastructure.git.runner import GitRunner
from aconsole.core.infrastructure.persistence.cache.store import StaleScratchFenceError
from aconsole.core.infrastructure.persistence.repositories.attempts import AttemptDatabase
from aconsole.core.infrastructure.persistence.repositories.code_graph import persistent_impact
from aconsole.core.infrastructure.pipeline import micro_profile
from aconsole.core.infrastructure.vault.sync import NodeNote

_LOG = logging.getLogger(__name__)
_NODE_NOT_FOUND = 4002
_HUMAN_TAB = "human"
_HELD = frozenset({NodeStatus.IN_PROGRESS, NodeStatus.VERIFYING})
_SUMMARY = 300
# Revisions of one plan kept to answer `since`: enough for several tabs reading it in turn.
_REVISIONS = 16
_PIPELINE_SCOPE_MAX = 20
# What one `plan_get` showed: whether it was detailed, and each node as it read.
type _Shown = tuple[bool, dict[str, str]]
_DIFF = 4000
# Bound the UTF-8 tool text, including answers assembled from many stored records.
_REPLY_BYTES = 256 * 1024
type _ItemText = Annotated[str, Field(max_length=2000)]
type _KeyText = Annotated[str, Field(max_length=10)]
_GIT_RUNNER = GitRunner(timeout_s=5.0)
_LINE = re.compile(r"^(.+)#L(\d+)-(\d+)$")
_URL = re.compile(r"^https?://\S+$")


class _Frozen(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class PipelineReply(RootModel[dict[str, object]]):
    """Keep stable context blocks and volatile lease fields in their deliberate wire order."""


class NodeIn(_Frozen):
    """One node offered to ``plan_create`` or ``plan_add_nodes``."""

    key: str = Field(pattern=r"^[A-Z]{1,4}-\d{1,4}$")
    title: str = Field(min_length=3, max_length=120)
    objective: str = Field(max_length=2000)
    depends_on: list[_KeyText] = Field(default_factory=list, max_length=100)
    parent: _KeyText | None = None
    related: list[_KeyText] = Field(default_factory=list, max_length=100)
    owner_agent: Literal["claude", "codex"] | None = None
    paths: list[_ItemText] = Field(default_factory=list, max_length=100)
    acceptance: list[_ItemText] = Field(default_factory=list, max_length=100)
    level: Literal[0, 1] = 1
    pipeline: bool = False
    invariants: _ItemText | None = None
    gate_profile: dict[str, object] | None = None
    test_cmd: _ItemText | None = None
    review_required: bool = False
    output_format: _ItemText | None = None
    boundaries: _ItemText | None = None


class NodeRef(_Frozen):
    key: str
    node_id: str


class PlanCreated(_Frozen):
    plan_id: str
    nodes: list[NodeRef]
    canvas_note: str


class PlanSaved(_Frozen):
    plan_id: str
    title: str
    phase: str
    status: str
    nodes: list[NodeRef]


class PlanDeleted(_Frozen):
    plan_id: str


class Added(_Frozen):
    added: list[NodeRef]
    unchanged: list[str]


class NodeBrief(_Frozen):
    node_id: str
    key: str
    title: str
    status: str
    ready: bool
    pipeline: bool = False
    contract_sha256: str | None = None
    contract_error: str | None = None
    owner: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    objective: str | None = None
    acceptance: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    lease_id: str | None = None
    fence: int | None = None
    expires_at: str | None = None


class PlanGot(_Frozen):
    title: str
    goal: str
    phase: str
    counts: dict[str, int]
    nodes: list[NodeBrief]
    revision: str
    changed_only: bool = False


class ReadyNode(_Frozen):
    node_id: str
    key: str
    title: str
    priority: float
    paths: list[str]
    owner_agent: str | None


class PlanReady(_Frozen):
    nodes: list[ReadyNode]


class Brief(_Frozen):
    objective: str
    acceptance: list[str]
    paths: list[str]
    boundaries: str | None
    output_format: str | None


class CtxItem(_Frozen):
    id: str
    type: str
    title: str
    summary: str
    source: str
    note_ref: str
    stale: bool


class CtxResult(_Frozen):
    items: list[CtxItem]
    truncated: bool
    verified_commit: str | None = None


class Claimed(_Frozen):
    lease_id: str
    fence: int
    expires_at: str
    brief: Brief
    context: CtxResult


class Updated(_Frozen):
    status: str
    expires_at: str | None


class Attached(_Frozen):
    artifact_id: str


class KbAdded(_Frozen):
    kb_id: str
    note_ref: str


class Impact(_Frozen):
    layers: list[list[str]]
    truncated: bool
    canvas_note: str


class HandoverResult(_Frozen):
    handover_id: str | None = None
    note_ref: str | None = None
    goal: str = ""
    done: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    failing_checks: list[str] = Field(default_factory=list)
    diff_stat: str = ""
    nodes: list[NodeBrief] = Field(default_factory=list)


class PlanRow(_Frozen):
    plan_id: str
    title: str
    done: int
    total: int
    updated_at: str


class PlanList(_Frozen):
    plans: list[PlanRow]


class _ToolError(Exception):
    """A domain refusal. The MCP wrapper turns it into ``isError`` plus ``hint``."""

    def __init__(self, result: CallToolResult) -> None:
        self.result: CallToolResult = result
        super().__init__(_result_text(result))


def _result_text(result: CallToolResult) -> str:
    for block in result.content:
        if isinstance(block, TextContent):
            return block.text
    return ""


_CURRENT: ContextVar[TabContext | None] = ContextVar("aconsole_mcp_tab", default=None)


def bind_tab(context: TabContext) -> tuple[object, Callable[[], None]]:
    """Select the tab for the current HTTP request. The second value resets it."""
    token = _CURRENT.set(context)

    def reset() -> None:
        _CURRENT.reset(token)

    return token, reset


def active(bound: TabContext) -> TabContext:
    """The bearer tab when the request had one, otherwise the server's default tab."""
    found = _CURRENT.get()
    return bound if found is None else found


def publish(fn: Callable[..., object], sent: Callable[[int], None] | None = None) -> Callable[..., object]:
    """Keep the success return type as the tool schema, and surface ``_ToolError`` as the result.

    ``sent`` hears the bytes of text each answer puts in the agent's context.
    Keep this wrapper synchronous: the pinned MCP SDK dispatches it with its bounded thread worker,
    preserving the bearer ContextVar and waiting for synchronous work before cancellation completes.
    """
    signature = inspect.signature(fn, eval_str=True)

    def wrapped(*args: object, **kwargs: object) -> object:
        try:
            raw = fn(*args, **kwargs)
            # A CallToolResult is a pydantic model too, and already what the agent reads.
            result = compact(raw) if isinstance(raw, BaseModel) and not isinstance(raw, CallToolResult) else raw
        except _ToolError as exc:
            result = exc.result
        if sent is not None and isinstance(result, CallToolResult):
            sent(len(_result_text(result).encode()))
        return result

    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    wrapped.__annotations__ = dict(fn.__annotations__)
    wrapped.__signature__ = signature
    return wrapped


def compact(result: BaseModel) -> CallToolResult:
    """The answer as the agent reads it: JSON without indentation, and without fields left empty or at their default.

    Every byte here is a token in the agent's context, on every call. Agents read the text, so no structured copy of
    the same answer goes beside it, and the tools publish no output schema.
    """
    lean = result.model_dump(mode="json", exclude_defaults=True, exclude_none=True)
    encoded = pydantic_core.to_json(lean)
    if len(encoded) > _REPLY_BYTES:
        _stop("RESPONSE_TOO_LARGE", "the tool answer exceeds 256 KiB")
    return CallToolResult(content=[TextContent(type="text", text=encoded.decode())])


@dataclass(frozen=True, slots=True, kw_only=True)
class TabContext:
    """The tab a bearer token resolved. Tools never read the token itself."""

    tab_id: str
    project_id: str
    worktree: Path | None
    agent: str
    # None supports trusted in-process callers predating tab modes. Live tokens always bind a mode.
    mode: Literal["plan", "execute"] | None = None


@dataclass
class _Meta:
    node_id: str
    key: str
    title: str
    objective: str
    acceptance: tuple[str, ...]
    paths: tuple[str, ...]
    owner: str | None
    boundaries: str | None
    output_format: str | None
    parent: str | None
    related: tuple[str, ...]
    test_cmd: str | None = None
    max_attempts: int = 4
    contract_sha256: str | None = None
    blast_radius: tuple[str, ...] | None = None
    baseline_children: int = 0
    artifacts: dict[tuple[str, str], str] = field(default_factory=dict)


@dataclass
class _Stored:
    plan_id: str
    project_id: str
    title: str
    goal: str
    dag: Plan
    meta: dict[str, _Meta]
    deps: dict[str, tuple[str, ...]]
    updated_at: datetime
    created_at: datetime
    topology: PlanTopology | None = None


@dataclass
class _Fact:
    kb_id: str
    project_id: str
    kind: str
    title: str
    body: str
    source: str
    code_refs: tuple[str, ...]
    stale: bool
    note_ref: str
    verified_commit: str | None = None


@dataclass
class _Handover:
    handover_id: str
    note_ref: str
    goal: str
    done: tuple[str, ...]
    decisions: tuple[str, ...]
    open_questions: tuple[str, ...]
    next_steps: tuple[str, ...]
    failing_checks: tuple[str, ...]


class CodeGraph(Protocol):
    def resolve(self, project_id: str, target: str) -> str | None: ...

    def dependents(self, project_id: str, kinds: frozenset[str] | None = None) -> dict[str, tuple[str, ...]]: ...


class PlanBoard:
    """Plans, knowledge, and handovers for one core process. Plans are also kept in a `PlanStore`."""

    def __init__(self, now: Callable[[], datetime] | None = None, *, checked_completion: bool = False) -> None:
        self._checked_completion: bool = checked_completion
        self._now: Callable[[], datetime] = now if now is not None else _clock
        self._lock: threading.RLock = threading.RLock()
        self._seq: int = 0
        self._plans: dict[str, _Stored] = {}
        self._facts: dict[str, _Fact] = {}
        self._handovers: dict[str, _Handover] = {}
        self._store: PlanStore | None = None
        self._saved: dict[str, StoredPlan] = {}
        self._attempts: AttemptDatabase | None = None
        self.directions: DirectionVerifier | None = None
        self.pipeline: PipelineService | None = None
        self.pipeline_actor: Callable[[str], PipelineActor] | None = None
        self.on_attention: Callable[[str, str, str, str], None] | None = None
        self._memory_inbox: dict[str, StoredPlanChange] = {}
        self._knowledge: KnowledgeStore | None = None
        self.on_changed: Callable[[PlanView], None] | None = None
        self.on_removed: Callable[[str], None] | None = None
        self.note_ref: Callable[[str, str], str | None] = lambda _plan, _key: None
        self.code_graph: CodeGraph | None = None
        self._epoch: str = secrets.token_hex(3)
        self._shown: int = 0
        self._seen: dict[str, OrderedDict[str, _Shown]] = {}

    def use_store(self, store: PlanStore) -> None:
        """Take the plans `store` kept. From here on every change is saved there."""
        with self._lock:
            self._store = store
            for saved in store.load():
                self._plans[saved.id] = _restored(saved, checked_completion=self._checked_completion)
                self._saved[saved.id] = saved
                self._seq = max(self._seq, _number(saved.id), *(_number(node.id) for node in saved.nodes))

    def use_knowledge(self, store: KnowledgeStore) -> None:
        """Take the facts and handovers `store` kept. From here on each one is committed before it is answered."""
        with self._lock:
            self._knowledge = store
            facts, handovers = store.load()
            for fact in facts:
                self._facts[fact.id] = _Fact(
                    kb_id=fact.id,
                    project_id=fact.project_id,
                    kind=fact.kind,
                    title=fact.title,
                    body=fact.body,
                    source=fact.source,
                    code_refs=fact.code_refs,
                    stale=fact.stale,
                    note_ref=fact.note_ref,
                    verified_commit=fact.verified_commit,
                )
                self._seq = max(self._seq, _number(fact.id))
            for packet in handovers:
                self._seq = max(self._seq, _number(packet.id))
                if packet.tab_id is None:
                    continue
                self._handovers[packet.tab_id] = _Handover(
                    handover_id=packet.id,
                    note_ref=packet.note_ref,
                    goal=packet.goal,
                    done=packet.done,
                    decisions=packet.decisions,
                    open_questions=packet.open_questions,
                    next_steps=packet.next_steps,
                    failing_checks=packet.failing_checks,
                )

    def views(self, project_id: str | None = None, *, active_only: bool = False) -> tuple[PlanView, ...]:
        """Plans for `app/snapshot` and `plan/list`."""
        with self._lock:
            found = tuple(
                self._view(stored)
                for stored in self._plans.values()
                if project_id is None or stored.project_id == project_id
            )
        return tuple(view for view in found if view.status == "active") if active_only else found

    def view(self, plan_id: str) -> PlanView | None:
        with self._lock:
            stored = self._plans.get(plan_id)
            return None if stored is None else self._view(stored)

    def export_plan(self, plan_id: str) -> StoredPlan:
        with self._lock:
            stored = self._plans.get(plan_id)
            if stored is None:
                raise DomainError(code=4001, name="PLAN_NOT_FOUND", retryable=False)
            return self._state(stored)

    def freeze(self, plan_id: str, radii: dict[str, tuple[str, ...] | None], *, graph_present: bool) -> PlanView:
        with self._lock:
            frozen = freeze_plan(self.export_plan(plan_id), radii, at=self._now(), graph_present=graph_present)
            current = self._plans[plan_id]
            if current.topology is not None and current.topology.phase == "frozen":
                return self._view(current)
            stored = _restored(frozen, checked_completion=self._checked_completion)
            self._plans[plan_id] = stored
            self._commit(stored)
            return self._view(stored)

    def apply_change(
        self,
        plan_id: str,
        change_id: str,
        radius: Callable[[StoredPlan], tuple[dict[str, tuple[str, ...] | None], bool]],
    ) -> PlanView:
        with self._lock:
            before = self.export_plan(plan_id)
            if before.topology is not None and change_id in before.topology.applied_changes:
                return self._view(self._plans[plan_id])
            if not isinstance(self._store, PlanChangeStore):
                message = "a durable decision store is required"
                raise StoreWriteError(message)
            change = self._store.load_change(change_id)
            if change is None:
                raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
            try:
                offered = TypeAdapter(list[NodeIn]).validate_python(change.payload.get("nodes", []))
            except ValueError as exc:
                raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False) from exc
            offered = _planning_nodes(
                TabContext(tab_id="human", project_id=before.project_id, worktree=None, agent="human", mode="plan"),
                offered,
            )
            proof: str | None = None
            receipt_id = change.payload.get("proof_receipt")
            if isinstance(receipt_id, str) and self._attempts is not None:
                receipt = self._attempts.receipt(receipt_id)
                if (
                    receipt is not None
                    and not receipt.passed
                    and receipt.node_id == change.node_id
                    and receipt.node_id in {node.id for node in before.nodes}
                    and receipt.tier in {"micro", "blast"}
                ):
                    proof = receipt_id
            candidate = _restored(before, checked_completion=self._checked_completion)
            candidate.topology = replace(topology_for(before), phase="draft")
            _ = self._insert(candidate, offered)
            changed = apply_plan_change(
                before, self._state(candidate), change, offered, proof_receipt=proof, radius=radius
            )
            candidate = _restored(changed, checked_completion=self._checked_completion)
            self._plans[plan_id] = candidate
            self._commit(candidate, change_id=change_id)
            return self._view(candidate)

    def set_node_status(self, node_id: str, status: str, note: str | None) -> PlanNodeView:
        """The user moves a node. A human holds no fence, so the node's own fence is used. A note serves as evidence."""
        if status == "done":
            with self._lock:
                stored, meta = self._find_node(node_id)
                pipeline = stored.dag.get(meta.key).pipeline
                direction = stored.dag.get(meta.key).level == 0
            if direction:
                if self.directions is None:
                    raise DomainError(code=4018, name="GATE_RECEIPT_REQUIRED", retryable=False)
                _ = self.directions.run(node_id, None, None, check_only=True)
            if pipeline:
                _ = self._blast_node(stored, meta, check_only=True)
        with self._lock:
            stored, meta = self._find_node(node_id)
            now = self._now()
            target = NodeStatus(status)
            current = stored.dag.get(meta.key)
            if current.status in {NodeStatus.BLOCKED, NodeStatus.FAILED} and target is NodeStatus.PENDING:
                request = next((entry for entry in self.inbox(stored.plan_id) if entry.node_id == node_id), None)
                synthetic = request or StoredPlanChange(
                    id="", plan_id=stored.plan_id, node_id=node_id, kind="human", status="open", payload={}
                )
                view = self._decide(stored, synthetic, "unblock", note, "human")
                return next(node for node in view.nodes if str(node.id) == node_id)
            _ = stored.dag.transition(
                Move(
                    key=meta.key,
                    to=target,
                    role=Role.HUMAN,
                    at=now,
                    tab_id=_HUMAN_TAB if target is NodeStatus.IN_PROGRESS else None,
                    fence=stored.dag.get(meta.key).fence if stored.dag.get(meta.key).lease_expires_at else None,
                    evidence=note if target is not NodeStatus.PENDING else None,
                    note=note,
                )
            )
            self._commit(stored)
            view = self._view(stored)
            return next(item for item in view.nodes if str(item.id) == node_id)

    def inbox(self, plan_id: str) -> tuple[StoredPlanChange, ...]:
        with self._lock:
            _ = self.export_plan(plan_id)
            if isinstance(self._store, PlanInboxStore):
                return self._store.inbox(plan_id)
            return tuple(
                entry for entry in self._memory_inbox.values() if entry.plan_id == plan_id and entry.status == "open"
            )

    def decide(
        self, plan_id: str, request_id: str, action: str, note: str | None, *, decided_by: str = "human"
    ) -> PlanView:
        with self._lock:
            _ = self.export_plan(plan_id)
            request = (
                self._store.load_change(request_id)
                if isinstance(self._store, PlanInboxStore)
                else self._memory_inbox.get(request_id)
            )
            return self._decide(self._plans[plan_id], request, action, note, decided_by)

    def _decide(
        self, stored: _Stored, request: StoredPlanChange | None, action: str, note: str | None, decided_by: str
    ) -> PlanView:
        before = self._state(stored)
        move = review_move(before, request, action=action, note=note, decided_by=decided_by)
        if move.replay:
            return self._view(stored)
        candidate = _restored(before, checked_completion=self._checked_completion)
        if move.target is not None:
            meta = next(meta for meta in candidate.meta.values() if meta.node_id == move.node_id)
            node = candidate.dag.get(meta.key)
            _ = candidate.dag.transition(
                Move(
                    key=meta.key,
                    to=NodeStatus(move.target),
                    role=Role.HUMAN,
                    at=self._now(),
                    note=move.note,
                    evidence=move.note,
                    fence=node.fence if node.lease_expires_at is not None else None,
                )
            )
        decision = StoredPlanDecision(
            id=uuid4().hex[:26],
            request_id=request.id if request and request.id else None,
            node_id=move.node_id,
            action=action,
            note=move.note,
            at=self._now(),
            decided_by=decided_by,
        )
        self._plans[stored.plan_id] = candidate
        self._commit(candidate, decision=decision)
        return self._view(candidate)

    def escalate(self, actor: PipelineActor, node_id: str, fence: int, kind: str, reason: str) -> str:
        with self._lock:
            ctx = _actor_context(actor)
            stored, meta = self._node(ctx, node_id)
            node = stored.dag.get(meta.key)
            if node.status is NodeStatus.BLOCKED and node.fence == fence:
                replay = next(
                    (
                        entry
                        for entry in self.inbox(stored.plan_id)
                        if entry.node_id == node_id
                        and entry.body == reason
                        and entry.payload.get("source_tab_id") == actor.tab_id
                        and entry.payload.get("kind") == kind
                    ),
                    None,
                )
                if replay is not None:
                    return replay.id
            merged = self._review_holder(stored, meta, actor.tab_id, fence)
            if node.tab_id != actor.tab_id and not merged:
                _stop("LEASE_HELD", "only the active executor may escalate this node")
            try:
                _ = stored.dag.transition(
                    Move(
                        key=meta.key,
                        to=NodeStatus.BLOCKED,
                        role=Role.AGENT,
                        at=self._now(),
                        fence=None if merged else fence,
                        tab_id=actor.tab_id,
                        note=reason,
                    )
                )
            except DomainError as exc:
                _domain(exc)
            entry = self._attention(stored, meta, actor.tab_id, kind, reason)
            self._commit(stored, attention=entry)
            return entry.id

    def _attention(self, stored: _Stored, meta: _Meta, tab_id: str, kind: str, reason: str) -> StoredPlanChange:
        return StoredPlanChange(
            id=uuid4().hex[:26],
            plan_id=stored.plan_id,
            node_id=meta.node_id,
            tab_id=tab_id,
            kind="replan",
            body=reason,
            status="open",
            created_at=self._now(),
            payload={"kind": kind, "fence": stored.dag.get(meta.key).fence, "source_tab_id": tab_id},
        )

    def record_merge(self, *, node_id: str | None, tab_id: str | None, oid: str) -> None:
        """Keep the merged commit on its node, and move the node only as far as its own rules allow.

        Safe to run twice: a commit is attached once, and a node already past its step stays there. A node that is
        gone has nothing to record. A store that refuses raises `StoreWriteError`, and the caller tries again.
        """
        with self._lock:
            found = self._merged_node(node_id, tab_id)
            if found is None:
                return
            stored, meta = found
            if ("commit", oid) not in meta.artifacts:
                meta.artifacts["commit", oid] = self._id("art")
            node = stored.dag.get(meta.key)
            target = after_merge(node.status, review_required=node.review_required) if not node.pipeline else None
            evidence = f"merged as {oid}"
            attention = False
            if node.pipeline:
                merged = stored.dag.record_pipeline_merge(meta.key, evidence=evidence)
                attention = merged.status is NodeStatus.VERIFYING and (
                    node.status is not NodeStatus.VERIFYING or node.lease_id is not None
                )
            try:
                if target is not None:
                    _ = stored.dag.transition(
                        Move(
                            key=meta.key,
                            to=target,
                            role=Role.HUMAN,
                            at=self._now(),
                            tab_id=None,
                            fence=node.fence if node.lease_expires_at else None,
                            evidence=evidence,
                            note=evidence,
                        )
                    )
            except DomainError as exc:
                # The lease ran out or the node moved. The commit is still recorded; a person takes the node on.
                _LOG.info("plan node %s stays where it is after the merge: %s", meta.node_id, exc)
            try:
                self._commit(stored)
            except _ToolError as exc:
                raise StoreWriteError(str(exc)) from exc
        if attention:
            try:
                _ = self._blast_node(stored, meta, check_only=False)
            except (DomainError, _ToolError, GateRunnerUnavailableError) as exc:
                _LOG.info("merged node %s awaits a blast gate: %s", meta.node_id, exc)
            if self.on_attention is not None:
                self.on_attention(stored.plan_id, meta.node_id, "verify", "Merged pipeline node requires review")

    def use_attempts(self, attempts: AttemptDatabase) -> None:
        self._attempts = attempts

    def contract(self, actor: PipelineActor, node_id: str, fence: int, *, review: bool = False) -> PipelineContract:
        ctx = _actor_context(actor)
        with self._lock:
            stored, meta = self._node(ctx, node_id)
            current = stored.dag.get(meta.key)
            if current.status is NodeStatus.BLOCKED and current.note == "ATTEMPTS_EXHAUSTED":
                _stop("ATTEMPTS_EXHAUSTED", "the durable retry budget is exhausted")
            merged = review and self._review_holder(stored, meta, actor.tab_id, fence)
            if current.tab_id != actor.tab_id and not merged:
                _stop("LEASE_HELD", "the active lease belongs to another tab")
            try:
                node = current if merged else stored.dag.heartbeat(meta.key, fence=fence, at=self._now())
            except DomainError as exc:
                _domain(exc)
            if (
                not node.pipeline
                or not meta.contract_sha256
                or stored.topology is None
                or stored.topology.phase != "frozen"
            ):
                _stop("NODE_CONTRACT", "pipeline steps require a frozen module contract")
            parent = stored.meta.get(meta.parent or "")
            if parent is None or len(meta.paths) > _PIPELINE_SCOPE_MAX:
                _stop("NODE_CONTRACT", "pipeline scope requires an L0 parent and at most 20 paths")
            direction = stored.dag.get(parent.key)
            profile = {} if direction.gate_profile is None else dict(direction.gate_profile)
            self._commit(stored)
            return PipelineContract(
                plan_id=stored.plan_id,
                node_id=node_id,
                max_attempts=meta.max_attempts,
                direction=DirectionContext(
                    key=parent.key,
                    invariants=direction.invariants or "",
                    scope=direction.paths,
                    gate_profile_json=json.dumps(profile, sort_keys=True),
                ),
                module=ModuleContext(
                    key=meta.key,
                    direction_key=parent.key,
                    objective=meta.objective,
                    acceptance=meta.acceptance,
                    paths=meta.paths,
                    test_cmd=meta.test_cmd,
                    contract_sha256=meta.contract_sha256,
                    boundaries=meta.boundaries,
                    output_format=meta.output_format,
                    blast_radius=meta.blast_radius,
                ),
                lease=LeaseTail(lease_id=node.lease_id or "", fence=fence, expires_at=_stamp(node.lease_expires_at)),
                profile=micro_profile(profile, location=f"{parent.key}.gate_profile"),
                gates_sha256=stored.topology.gates_sha256 or "",
            )

    def _review_holder(self, stored: _Stored, meta: _Meta, tab_id: str, fence: int) -> bool:
        node = stored.dag.get(meta.key)
        history = () if self._attempts is None else self._attempts.attempts(meta.node_id)
        return (
            node.pipeline
            and node.status is NodeStatus.VERIFYING
            and node.lease_id is None
            and node.fence == fence
            and bool(history)
            and history[-1].tab_id == tab_id
        )

    def _blast_node(self, stored: _Stored, meta: _Meta, *, check_only: bool) -> dict[str, object]:
        history = () if self._attempts is None else self._attempts.attempts(meta.node_id)
        if not history or not history[-1].tab_id or self.pipeline_actor is None or self.pipeline is None:
            raise DomainError(code=4018, name="GATE_RECEIPT_REQUIRED", retryable=False)
        actor = self.pipeline_actor(history[-1].tab_id)
        if actor.project_id != stored.project_id:
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
        try:
            return self.pipeline.blast_gate(actor, meta.node_id, stored.dag.get(meta.key).fence, check_only=check_only)
        except _ToolError as exc:
            raise DomainError(code=4018, name="GATE_RECEIPT_REQUIRED", retryable=False) from exc
        except GateRunnerUnavailableError as exc:
            raise DomainError(code=5010, name="GATE_RUNNER_UNAVAILABLE", retryable=True) from exc

    def scope(self, actor: PipelineActor, node_id: str, fence: int, *, review: bool = False) -> None:
        _ = self.contract(actor, node_id, fence, review=review)
        self.assert_scope(_actor_context(actor), node_id, review=review)

    def exhausted(self, actor: PipelineActor, node_id: str, fence: int) -> None:
        _ = self.escalate(actor, node_id, fence, "attempts", "ATTEMPTS_EXHAUSTED")

    def pipeline_step(  # noqa: PLR0913, C901 -- common dispatcher for pipeline tool signatures
        self,
        ctx: TabContext,
        action: str,
        node_id: str,
        fence: int,
        *,
        step: Step = "spec",
        symbols: tuple[str, ...] = (),
        tier: str = "micro",
        note: str | None = None,
        kind: str = "replan",
        reason: str = "",
    ) -> PipelineReply:
        _require_tab(ctx)
        if self.pipeline is None or self.pipeline_actor is None:
            _stop("NODE_CONTRACT", "pipeline storage is unavailable")
        actor = self.pipeline_actor(ctx.tab_id)
        if actor.project_id != ctx.project_id or actor.worktree != ctx.worktree:
            _stop("NODE_CONTRACT", "pipeline actor no longer matches its bearer context")
        try:
            if action == "begin":
                body = self.pipeline.begin(actor, node_id, fence)
            elif action == "submit":
                body = self.pipeline.submit(actor, node_id, fence, step, symbols)
            elif action == "escalate":
                body = self.pipeline.escalate(actor, node_id, fence, kind, reason)
            elif action == "reset":
                body = self.pipeline.reset(actor, node_id, fence)
            else:
                body = self.pipeline.gate_run(actor, node_id, fence, tier)
        except DomainError as exc:
            _domain(exc)
        except StaleScratchFenceError:
            _stop("LEASE_STALE_FENCE", "scratch state belongs to a newer lease")
        except GateRunnerUnavailableError:
            _stop("GATE_RUNNER_UNAVAILABLE", "the tab container cannot execute checks")
        with self._lock:
            stored, _meta = self._node(ctx, node_id)
            node = stored.dag.get(_meta.key)
            if note is not None and node.lease_expires_at is not None:
                _ = stored.dag.heartbeat(_meta.key, fence=fence, at=self._now(), note=note)
            self._commit(stored)
        return PipelineReply(body)

    def scope_for_tab(self, tab_id: str, project_id: str | None) -> tuple[str, ...] | None:
        with self._lock:
            found = self._merged_node(None, tab_id)
            if found is None or found[0].project_id != project_id:
                return None
            stored, meta = found
            node = stored.dag.get(meta.key)
            if node.lease_expires_at is None or node.lease_expires_at <= self._now():
                return None
            return node.paths

    def assert_scope(self, ctx: TabContext, node_id: str, *, review: bool = False) -> None:
        """Delegate the submitted delta to the domain scope rule before advancing L2."""
        with self._lock:
            stored, meta = self._node(ctx, node_id)
            current = stored.dag.get(meta.key)
            merged = review and self._review_holder(stored, meta, ctx.tab_id, current.fence)
            if current.tab_id != ctx.tab_id and not merged:
                _stop("LEASE_HELD", "the active lease belongs to another tab")
            self._verify_scope(ctx, stored, meta)

    def _verify_scope(self, ctx: TabContext, stored: _Stored, meta: _Meta) -> None:
        current = stored.dag.get(meta.key)
        if not current.pipeline:
            return
        history = () if self._attempts is None else self._attempts.attempts(meta.node_id)
        if not history or ctx.worktree is None:
            raise DomainError(code=4015, name="SCOPE_VIOLATION", retryable=False)
        checked = check_scope(current.paths, GitDiff().changed_paths(ctx.worktree, history[-1].base_oid))
        if checked.review_required and not current.review_required:
            stored.dag.edit(meta.key, paths=current.paths, review_required=True)
            self._commit(stored)
        checked.require_allowed()

    def node_held_by(self, tab_id: str) -> str | None:
        with self._lock:
            found = self._merged_node(None, tab_id)
            return None if found is None else found[1].node_id

    def facts_to_check(self, project_id: str) -> tuple[StoredFact, ...]:
        """Live facts that name code and say which commit they were true at."""
        now = self._now()
        with self._lock:
            return tuple(
                StoredFact(
                    id=fact.kb_id,
                    project_id=fact.project_id,
                    kind=fact.kind,
                    title=fact.title,
                    body=fact.body,
                    source=fact.source,
                    code_refs=fact.code_refs,
                    stale=fact.stale,
                    note_ref=fact.note_ref,
                    updated_at=now,
                    verified_commit=fact.verified_commit,
                )
                for fact in self._facts.values()
                if fact.project_id == project_id and not fact.stale and fact.verified_commit and fact.code_refs
            )

    def _merged_node(self, node_id: str | None, tab_id: str | None) -> tuple[_Stored, _Meta] | None:
        """The node the tab was opened for, or else the one it holds a lease on."""
        for stored in self._plans.values():
            for meta in stored.meta.values():
                if node_id is not None and meta.node_id == node_id:
                    return stored, meta
                node = stored.dag.get(meta.key)
                if node_id is None and tab_id is not None and node.tab_id == tab_id and node.status in _HELD:
                    return stored, meta
        return None

    def plan_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._plans)

    def node_notes(self, plan_id: str) -> tuple[NodeNote, ...]:
        """What the vault should hold for one plan."""
        with self._lock:
            stored = self._plans.get(plan_id)
            if stored is None:
                return ()
            # Every note uses the same immutable graph snapshot. Build it once while holding the board lock.
            graph = Owners(
                known_keys=frozenset(stored.meta),
                edges=tuple((key, blocker) for key, blockers in stored.deps.items() for blocker in blockers),
                blocker_status=tuple((key, stored.dag.get(key).status.value) for key in stored.meta),
            )
            return tuple(_node_note(stored, meta, graph) for meta in stored.meta.values())

    def apply_note(self, plan_id: str, key: str, note: VaultNote) -> None:
        """A person's edit the merge accepted. A status the rules refuse stays as it was, and the note follows."""
        with self._lock:
            stored = self._plans.get(plan_id)
            meta = None if stored is None else stored.meta.get(key)
            if stored is None or meta is None:
                return
            require_note_contract(self._state(stored), key, note)
            if stored.dag.get(key).status in {NodeStatus.BLOCKED, NodeStatus.FAILED} and note.status == "pending":
                _ = self.set_node_status(meta.node_id, "pending", "Unblocked through vault note")
                stored = self._plans[plan_id]
                meta = stored.meta[key]
            meta.title = note.title
            meta.objective = note.objective
            meta.acceptance = note.acceptance
            meta.owner = note.owner_agent or None
            meta.paths = note.paths
            stored.dag.edit(key, paths=note.paths, review_required=note.review_required)
            try:
                blockers = tuple(item for item in (resolve_ref(raw) for raw in note.depends_on) if item is not None)
                stored.dag.set_blockers(key, blockers)
                stored.deps[key] = blockers
                self._move_by_hand(stored, key, note.status)
            except (DomainError, ValueError) as exc:
                _LOG.warning("the note of %s asks for a move the plan refuses: %s", key, exc)
            self._commit(stored)

    def _move_by_hand(self, stored: _Stored, key: str, status: str) -> None:
        current = stored.dag.get(key)
        if current.level == 0 and status == "done":
            raise DomainError(code=4018, name="GATE_RECEIPT_REQUIRED", retryable=False)
        if current.status.value == status:
            return
        target = NodeStatus(status)
        _ = stored.dag.transition(
            Move(
                key=key,
                to=target,
                role=Role.HUMAN,
                at=self._now(),
                tab_id=_HUMAN_TAB if target is NodeStatus.IN_PROGRESS else None,
                fence=current.fence if current.lease_expires_at else None,
            )
        )

    def _find_node(self, node_id: str) -> tuple[_Stored, _Meta]:
        for stored in self._plans.values():
            for meta in stored.meta.values():
                if meta.node_id == node_id:
                    return stored, meta
        raise DomainError(code=_NODE_NOT_FOUND, name="NODE_NOT_FOUND", retryable=False, extra=(node_id,))

    def _commit(
        self,
        stored: _Stored,
        *,
        change_id: str | None = None,
        attention: StoredPlanChange | None = None,
        decision: StoredPlanDecision | None = None,
    ) -> None:
        """Every change goes through here: the store, then whoever shows plans."""
        stored.updated_at = self._now()
        if self._store is not None:
            state = self._state(stored)
            try:
                if attention is not None and isinstance(self._store, PlanInboxStore):
                    self._store.save_attention(state, attention)
                elif decision is not None and isinstance(self._store, PlanInboxStore):
                    self._store.save_decision(state, decision)
                elif change_id is not None and isinstance(self._store, PlanChangeStore):
                    self._store.save_change(state, change_id)
                else:
                    self._store.save(state)
            except StoreWriteError as exc:
                self._forget_unsaved(stored.plan_id)
                _stop("INTERNAL_ERROR", str(exc))
            self._saved[state.id] = state
        if attention is not None:
            self._memory_inbox[attention.id] = attention
        if decision is not None and decision.request_id in self._memory_inbox:
            entry = self._memory_inbox[decision.request_id]
            self._memory_inbox[entry.id] = replace(
                entry,
                status="rejected" if decision.action == "reject" else "applied",
                decided_by="human",
                decided_at=decision.at,
                payload={**entry.payload, "decision": decision.action, "decision_note": decision.note},
            )
        if self.on_changed is not None:
            self.on_changed(self._view(stored))
        if attention is not None and self.on_attention is not None:
            self.on_attention(stored.plan_id, attention.node_id or "", "replan", attention.body)

    def _forget_unsaved(self, plan_id: str) -> None:
        """Back to what the store holds, so that memory never promises more than a restart would keep."""
        saved = self._saved.get(plan_id)
        if saved is None:
            _ = self._plans.pop(plan_id, None)
        else:
            self._plans[plan_id] = _restored(saved, checked_completion=self._checked_completion)

    def _state(self, stored: _Stored) -> StoredPlan:
        layers = {key: layer for key, layer, _position in stored.dag.layout()}
        return StoredPlan(
            id=stored.plan_id,
            project_id=stored.project_id,
            title=stored.title,
            goal=stored.goal,
            status=_plan_status(stored),
            created_at=stored.created_at,
            updated_at=stored.updated_at,
            nodes=tuple(
                _stored_node(stored, item, layers.get(item.key, 0), self._now()) for item in stored.meta.values()
            ),
            topology=stored.topology,
        )

    def _view(self, stored: _Stored) -> PlanView:
        layers = {key: layer for key, layer, _position in stored.dag.layout()}
        now = self._now()
        attempts = (
            {}
            if self._attempts is None
            else self._attempts.summaries(tuple(meta.node_id for meta in stored.meta.values()))
        )
        nodes = tuple(
            self._node_view(stored, meta, layers.get(meta.key, 0), now, attempts.get(meta.node_id))
            for meta in stored.meta.values()
        )
        done = sum(node.status is NodeStatusView.DONE for node in nodes)
        return PlanView(
            id=PlanId(stored.plan_id),
            project_id=ProjectId(stored.project_id),
            title=stored.title,
            goal=stored.goal,
            status=_plan_status(stored),
            nodes=nodes,
            done=done,
            total=len(nodes),
            canvas=(
                NoteRef(f"plans/{stored.plan_id}/_plan.canvas")
                if self.note_ref(stored.plan_id, "_plan") is not None
                else None
            ),
            updated_at=stored.updated_at,
            phase="draft" if stored.topology is None else stored.topology.phase,
            warnings=("NO_CODE_GRAPH",)
            if stored.topology is not None
            and stored.topology.phase == "frozen"
            and any(stored.dag.get(meta.key).pipeline and meta.blast_radius is None for meta in stored.meta.values())
            else (),
        )

    def _node_view(
        self, stored: _Stored, meta: _Meta, layer: int, now: datetime, attempt: tuple[int, str | None] | None = None
    ) -> PlanNodeView:

        node = stored.dag.get(meta.key)
        by_key = {item.key: item.node_id for item in stored.meta.values()}
        note = self.note_ref(stored.plan_id, meta.key)
        return PlanNodeView(
            id=NodeId(meta.node_id),
            key=meta.key,
            title=meta.title,
            status=NodeStatusView(node.status.value),
            ready=_execution_ready(stored, meta),
            layer=layer,
            priority=stored.dag.priority(meta.key, now),
            depends_on=tuple(NodeId(by_key[key]) for key in stored.deps.get(meta.key, ()) if key in by_key),
            owner_agent=None if meta.owner is None else AgentKind(meta.owner),
            tab_id=None if node.tab_id is None or node.tab_id == _HUMAN_TAB else TabId(node.tab_id),
            lease_expires_at=node.lease_expires_at,
            note=None if note is None else NoteRef(note),
            evidence=node.evidence if node.status is NodeStatus.VERIFYING else None,
            level=0 if node.level == 0 else 1,
            parent=NodeId(by_key[meta.parent]) if meta.parent in by_key else None,
            pipeline=node.pipeline,
            attempt_no=0 if attempt is None else attempt[0],
            max_attempts=meta.max_attempts,
            last_verdict=None if attempt is None else attempt[1],
        )

    def create(self, ctx: TabContext, title: str, goal: str, nodes: list[NodeIn]) -> PlanCreated:
        """Create a plan, or return the one this project already has under ``title``."""
        _require_tab(ctx)
        nodes = _planning_nodes(ctx, nodes)
        with self._lock:
            stored = self._by_title(ctx.project_id, title)
            created = stored is None
            if stored is None:
                stored = self._new_plan(ctx.project_id, title, goal)
            try:
                added = self._insert(stored, nodes)
            except _ToolError:
                if created:
                    del self._plans[stored.plan_id]
                raise
            if created or added:
                self._commit(stored)
            return PlanCreated(plan_id=stored.plan_id, nodes=_refs(stored), canvas_note=_canvas(stored.plan_id))

    def save_document(
        self,
        project_id: str,
        title: str,
        goal: str,
        plan_id: str | None,
        nodes: tuple[PlanNodeSpec, ...] | None,
    ) -> PlanView:
        """Create a plan or replace a draft in one transaction. Omitted nodes leave the graph."""
        human = TabContext(tab_id=_HUMAN_TAB, project_id=project_id, worktree=None, agent="human", mode="plan")
        refusal: DomainError | None = None
        try:
            incoming = None if nodes is None else _planning_nodes(human, _node_inputs(nodes))
        except _ToolError as exc:
            refusal = _domain_error(exc)
            incoming = None
        if refusal is not None:
            raise refusal
        with self._lock:
            stored, created = self._document_target(project_id, title, goal, plan_id)
            try:
                return self._write_document(stored, created=created, title=title, goal=goal, nodes=incoming)
            except _ToolError as exc:
                if created:
                    _ = self._plans.pop(stored.plan_id, None)
                refusal = _domain_error(exc)
            except DomainError as exc:
                if created:
                    _ = self._plans.pop(stored.plan_id, None)
                refusal = exc
        raise refusal

    def save_for_tab(
        self,
        ctx: TabContext,
        title: str,
        goal: str,
        plan_id: str | None,
        nodes: list[NodeIn] | None,
    ) -> PlanSaved:
        """The planning tab's one-call create or draft replace. Execute tabs are refused."""
        _require_tab(ctx)
        if ctx.mode == "execute":
            _stop("PLAN_ROLE", "graph changes require a planning tab")
        try:
            view = self.save_document(
                ctx.project_id, title, goal, plan_id, None if nodes is None else _mcp_specs(nodes)
            )
        except DomainError as exc:
            _domain(exc)
        return PlanSaved(
            plan_id=str(view.id),
            title=view.title,
            phase=view.phase,
            status=view.status,
            nodes=[NodeRef(key=node.key, node_id=str(node.id)) for node in view.nodes],
        )

    def delete_for_tab(self, ctx: TabContext, plan_id: str) -> PlanDeleted:
        """Delete one plan of this project. A live lease keeps it."""
        _require_tab(ctx)
        if ctx.mode == "execute":
            _stop("PLAN_ROLE", "graph changes require a planning tab")
        view = self.view(plan_id)
        if view is None or str(view.project_id) != ctx.project_id:
            _stop("PLAN_NOT_FOUND", f"no plan {plan_id}")
        try:
            self.delete_plan(plan_id)
        except DomainError as exc:
            _domain(exc)
        return PlanDeleted(plan_id=plan_id)

    def delete_plan(self, plan_id: str) -> None:
        """Drop a plan that nobody is working. A live lease keeps the plan."""
        with self._lock:
            stored = self._plans.get(plan_id)
            if stored is None:
                raise DomainError(code=4001, name="PLAN_NOT_FOUND", retryable=False)
            if _busy(stored):
                raise DomainError(code=4006, name="LEASE_HELD", retryable=True)
            if self._store is not None:
                try:
                    self._store.delete(plan_id)
                except StoreWriteError as exc:
                    raise DomainError(code=-32603, name="INTERNAL_ERROR", retryable=False) from exc
            _ = self._plans.pop(plan_id, None)
            _ = self._saved.pop(plan_id, None)
            _ = self._seen.pop(plan_id, None)
            self._memory_inbox = {
                key: entry for key, entry in self._memory_inbox.items() if entry.plan_id != plan_id
            }
            if self.on_removed is not None:
                self.on_removed(plan_id)

    def _document_target(
        self, project_id: str, title: str, goal: str, plan_id: str | None
    ) -> tuple[_Stored, bool]:
        if plan_id is not None:
            stored = self._plans.get(plan_id)
            if stored is None or stored.project_id != project_id:
                raise DomainError(code=4001, name="PLAN_NOT_FOUND", retryable=False)
            return stored, False
        found = self._by_title(project_id, title)
        if found is not None:
            return found, False
        return self._new_plan(project_id, title, goal), True

    def _write_document(
        self,
        stored: _Stored,
        *,
        created: bool,
        title: str,
        goal: str,
        nodes: list[NodeIn] | None,
    ) -> PlanView:
        if not created:
            phase = "draft" if stored.topology is None else stored.topology.phase
            if phase != "draft" and (stored.title != title or stored.goal != goal or nodes is not None):
                raise DomainError(code=4011, name="PLAN_FROZEN", retryable=False)
            taken = self._by_title(stored.project_id, title)
            if taken is not None and taken.plan_id != stored.plan_id:
                raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False, extra=(title,))
            if nodes is not None and _busy(stored):
                raise DomainError(code=4006, name="LEASE_HELD", retryable=True)
        if nodes is None:
            changed = created or stored.title != title or stored.goal != goal
            stored.title = title
            stored.goal = goal
            if changed:
                self._commit(stored)
            return self._view(stored)
        if created:
            if nodes:
                _ = self._insert(stored, nodes)
            self._commit(stored)
            return self._view(stored)
        fresh = _Stored(
            plan_id=stored.plan_id,
            project_id=stored.project_id,
            title=title,
            goal=goal,
            dag=Plan(checked_completion=self._checked_completion, lease_namespace=stored.plan_id),
            meta={},
            deps={},
            updated_at=stored.updated_at,
            created_at=stored.created_at,
            topology=PlanTopology(),
        )
        if nodes:
            _ = self._insert(fresh, nodes)
        self._plans[stored.plan_id] = fresh
        self._commit(fresh)
        return self._view(fresh)

    def add_nodes(self, ctx: TabContext, plan_id: str, nodes: list[NodeIn]) -> Added:
        """Add missing keys. A key that already exists is unchanged. A cycle adds nothing."""
        _require_tab(ctx)
        nodes = _planning_nodes(ctx, nodes)
        with self._lock:
            stored = self._owned(ctx, plan_id)
            added = self._insert(stored, nodes)
            if added:
                self._commit(stored)
            fresh_keys = {ref.key for ref in added}
            unchanged = [item.key for item in nodes if item.key not in fresh_keys]
            return Added(added=added, unchanged=unchanged)

    def get(self, ctx: TabContext, plan_id: str, *, detailed: bool, since: str | None = None) -> PlanGot:
        """The plan. With ``since``, a revision an earlier answer gave, only the nodes that read differently now.

        A revision names what one answer showed, so a node counts as changed for any reason: its own move, a
        dependency finishing that made it ready, or a lease running out. A revision this core no longer remembers
        (an older process, or one pushed out by newer ones) gets the whole plan.
        """
        _require_tab(ctx)
        with self._lock:
            stored = self._owned(ctx, plan_id)
            briefs = [_brief(stored, meta, detailed=detailed) for meta in stored.meta.values()]
            marks = {brief.node_id: brief.model_dump_json() for brief in briefs}
            seen = self._seen.setdefault(plan_id, OrderedDict())
            revision = self._revision(seen, (detailed, marks))
            base = seen.get(since) if since is not None else None
            changed = base is not None and base[0] == detailed
            if base is not None and changed:
                briefs = [brief for brief in briefs if base[1].get(brief.node_id) != marks[brief.node_id]]
            return PlanGot(
                title=stored.title,
                goal=stored.goal,
                phase="draft" if stored.topology is None else stored.topology.phase,
                counts=_counts(stored),
                nodes=briefs,
                revision=revision,
                changed_only=changed,
            )

    def _revision(self, seen: OrderedDict[str, _Shown], shown: _Shown) -> str:
        """The revision for what ``shown`` holds: the last one that showed the same, else a new one."""
        for revision, earlier in reversed(seen.items()):
            if earlier == shown:
                seen.move_to_end(revision)
                return revision
        self._shown += 1
        revision = f"{self._epoch}.{self._shown}"
        seen[revision] = shown
        while len(seen) > _REVISIONS:
            _ = seen.popitem(last=False)
        return revision

    def ready(self, ctx: TabContext, plan_id: str | None, limit: int) -> PlanReady:
        _require_tab(ctx)
        with self._lock:
            stored = self._ready_plan(ctx, plan_id)
            ranked = [
                (stored.dag.priority(meta.key, self._now()), meta)
                for meta in stored.meta.values()
                if _execution_ready(stored, meta)
            ]
            ranked.sort(key=lambda item: (-item[0], item[1].key))
            chosen = [
                ReadyNode(
                    node_id=meta.node_id,
                    key=meta.key,
                    title=meta.title,
                    priority=score,
                    paths=list(meta.paths),
                    owner_agent=meta.owner,
                )
                for score, meta in ranked[:limit]
            ]
            return PlanReady(nodes=chosen)

    def claim(self, ctx: TabContext, node_id: str) -> Claimed:
        _require_tab(ctx)
        with self._lock:
            stored, meta = self._node(ctx, node_id)
            if issue := _profile_error(stored, meta):
                _stop("NODE_CONTRACT", issue)
            if stored.dag.get(meta.key).pipeline and not _pipeline_contract_ready(stored, meta):
                _stop("NODE_CONTRACT", "the module contract must be frozen before claiming this node")
            try:
                node = stored.dag.claim(meta.key, tab_id=ctx.tab_id, at=self._now())
            except DomainError as exc:
                _domain(exc)
            self._commit(stored)
            return Claimed(
                lease_id=node.lease_id or "",
                fence=node.fence,
                expires_at=_stamp(node.lease_expires_at),
                brief=Brief(
                    objective=meta.objective,
                    acceptance=list(meta.acceptance),
                    paths=list(meta.paths),
                    boundaries=meta.boundaries,
                    output_format=meta.output_format,
                ),
                context=self._ctx(ctx, node_id=node_id, query=None, max_items=6),
            )

    def _pipeline_transition(self, ctx: TabContext, node_id: str, fence: int | None, *, status: str) -> None:
        with self._lock:
            stored, meta = self._node(ctx, node_id)
            current = stored.dag.get(meta.key)
            if not current.pipeline:
                return
            merged = self._review_holder(stored, meta, ctx.tab_id, current.fence)
            if current.tab_id != ctx.tab_id and not merged:
                _stop("LEASE_HELD", "verification belongs to the active executor")
            if current.lease_id is not None:
                if fence is None:
                    _stop("LEASE_STALE_FENCE", "verification requires the active fence")
                _ = stored.dag.heartbeat(meta.key, fence=fence, at=self._now())
            elif fence is not None and fence != current.fence:
                _stop("LEASE_STALE_FENCE", "the merge retained a different fence")
            self._verify_scope(ctx, stored, meta)
        if self.pipeline is None or self.pipeline_actor is None:
            _stop("GATE_RECEIPT_REQUIRED", "pipeline verification is unavailable")
        actor = self.pipeline_actor(ctx.tab_id)
        if actor.project_id != ctx.project_id or actor.worktree != ctx.worktree:
            _stop("NODE_CONTRACT", "pipeline actor differs from the bearer context")
        result = self.pipeline.blast_gate(actor, node_id, current.fence, check_only=status == "done")
        if not result["passed"]:
            _stop("GATE_RECEIPT_REQUIRED", "the blast gate failed")

    def _pipeline_update(self, ctx: TabContext, node_id: str, fence: int | None, status: str | None) -> None:
        if status not in {"verifying", "done"}:
            return
        try:
            self._pipeline_transition(ctx, node_id, fence, status=status)
        except DomainError as exc:
            _domain(exc)
        except GateRunnerUnavailableError:
            _stop("GATE_RUNNER_UNAVAILABLE", "the tab container cannot execute checks")

    def update(  # noqa: PLR0913, PLR0917 -- status, note, and evidence travel with the fence
        self,
        ctx: TabContext,
        node_id: str,
        fence: int | None,
        status: str | None,
        note: str | None,
        evidence: str | None,
    ) -> Updated:
        _require_tab(ctx)
        self._pipeline_update(ctx, node_id, fence, status)
        with self._lock:
            stored, meta = self._node(ctx, node_id)
            now = self._now()
            current = stored.dag.get(meta.key)
            merged = self._review_holder(stored, meta, ctx.tab_id, current.fence)
            if merged and fence is not None and fence != current.fence:
                _stop("LEASE_STALE_FENCE", "the merge retained a different fence")
            if current.status in _HELD and current.tab_id != ctx.tab_id and not merged:
                _stop("LEASE_HELD", "the active lease belongs to another tab")
            try:
                if status is None:
                    if fence is None:
                        _stop("LEASE_STALE_FENCE", "a heartbeat requires the fence from node_claim")
                    node = stored.dag.heartbeat(meta.key, fence=fence, at=now, note=note)
                else:
                    node = stored.dag.transition(
                        Move(
                            key=meta.key,
                            to=NodeStatus(status),
                            role=Role.AGENT,
                            at=now,
                            tab_id=ctx.tab_id,
                            fence=None if merged else fence,
                            note=note,
                            evidence=evidence,
                        )
                    )
            except DomainError as exc:
                _domain(exc)
            except GateRunnerUnavailableError:
                _stop("GATE_RUNNER_UNAVAILABLE", "the tab container cannot execute checks")
            except ValueError:
                _stop("NODE_TRANSITION_INVALID", f"{status} is not a node status")
            attention = (
                self._attention(stored, meta, ctx.tab_id, "replan", note or "Node blocked")
                if status == "blocked" and current.status is not NodeStatus.BLOCKED
                else None
            )
            self._commit(stored, attention=attention)
            return Updated(status=node.status.value, expires_at=_optional_stamp(node.lease_expires_at))

    def attach(  # noqa: PLR0913, PLR0917 -- kind and ref identify the artifact
        self, ctx: TabContext, node_id: str, fence: int, kind: str, ref: str, title: str | None
    ) -> Attached:
        _require_tab(ctx)
        with self._lock:
            stored, meta = self._node(ctx, node_id)
            current = stored.dag.get(meta.key)
            if current.status in _HELD and current.tab_id != ctx.tab_id:
                _stop("LEASE_HELD", "the active lease belongs to another tab")
            try:
                _ = stored.dag.heartbeat(meta.key, fence=fence, at=self._now())
            except DomainError as exc:
                _domain(exc)
            if kind == "commit":
                if ctx.worktree is None:
                    _stop("NODE_CONTRACT", "a commit requires a Git worktree")
                try:
                    resolved = _GIT_RUNNER.read(
                        ctx.worktree, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}"
                    )
                except GitCommandError:
                    _stop("NODE_CONTRACT", "commit does not resolve in this worktree")
                ref = resolved.text.strip()
            key = (kind, ref)
            artifact_id = meta.artifacts.get(key)
            if artifact_id is None:
                artifact_id = self._id("art")
                meta.artifacts[key] = artifact_id
            _ = title
            self._commit(stored)
            return Attached(artifact_id=artifact_id)

    def ctx(self, ctx: TabContext, node_id: str | None, query: str | None, max_items: int) -> CtxResult:
        _require_tab(ctx)
        with self._lock:
            return self._ctx(ctx, node_id=node_id, query=query, max_items=max_items)

    def kb_add(  # noqa: PLR0913, PLR0917 -- MCP_TOOLS.md fixes these fields
        self,
        ctx: TabContext,
        kind: str,
        title: str,
        body: str,
        source: str,
        code_refs: list[str],
        supersedes: str | None,
    ) -> KbAdded:
        _require_tab(ctx)
        with self._lock:
            if kind == "fact":
                _check_source(ctx, source)
            if supersedes is not None:
                previous = self._facts.get(supersedes)
                if previous is None or previous.project_id != ctx.project_id:
                    _stop("NOTE_NOT_FOUND", f"no knowledge item {supersedes}")
            kb_id = self._id("kb")
            note_ref = f"aconsole://note/{kb_id}"
            fact = _Fact(
                kb_id=kb_id,
                project_id=ctx.project_id,
                kind=kind,
                title=title,
                body=body,
                source=source,
                code_refs=tuple(code_refs),
                stale=False,
                note_ref=note_ref,
                verified_commit=_verified(ctx.worktree),
            )
            self._keep_fact(fact, supersedes)
            if supersedes is not None:
                self._facts[supersedes].stale = True
            self._facts[kb_id] = fact
            return KbAdded(kb_id=kb_id, note_ref=note_ref)

    def _keep_fact(self, fact: _Fact, supersedes: str | None) -> None:
        if self._knowledge is None:
            return
        record = StoredFact(
            id=fact.kb_id,
            project_id=fact.project_id,
            kind=fact.kind,
            title=fact.title,
            body=fact.body,
            source=fact.source,
            code_refs=fact.code_refs,
            stale=fact.stale,
            note_ref=fact.note_ref,
            updated_at=self._now(),
            verified_commit=fact.verified_commit,
        )
        try:
            self._knowledge.save_fact(record, supersedes)
        except StoreWriteError as exc:
            _stop("INTERNAL_ERROR", str(exc))

    def impact(self, ctx: TabContext, target: str, hops: int, limit: int, kinds: list[str] | None) -> Impact:
        _require_tab(ctx)
        with self._lock:
            facts = self._target(ctx.project_id, target) is not None or bool(self._edges(ctx.project_id, kinds))
        if not facts:
            saved = persistent_impact(
                self.code_graph, ctx.project_id, target, hops, limit, None if kinds is None else frozenset(kinds)
            )
            if saved is not None:
                layers, truncated = saved
                return Impact(
                    layers=layers, truncated=truncated, canvas_note="aconsole://note/impact" if truncated else ""
                )
        # Outside the lock: building a graph can take a minute, and plans must keep moving.
        code_start, code_edges = self._code(ctx.project_id, target, kinds)
        with self._lock:
            start = self._target(ctx.project_id, target) or code_start
            edges = {**code_edges}
            for key, linked in self._edges(ctx.project_id, kinds).items():
                edges[key] = (*edges.get(key, ()), *linked)
            if start is None:
                _stop("IMPACT_TARGET_UNKNOWN", f"no impact target {target}")
            layers, truncated = impact_layers(edges, start, hops, limit)
            note = "aconsole://note/impact" if truncated else ""
            return Impact(layers=layers, truncated=truncated, canvas_note=note)

    def _code(
        self,
        project_id: str,
        target: str,
        kinds: list[str] | None,
    ) -> tuple[str | None, dict[str, tuple[str, ...]]]:
        """A failing extractor leaves the facts the board has. The tool's answer is still useful."""
        graph = self.code_graph
        if graph is None:
            return None, {}
        try:
            return graph.resolve(project_id, target), graph.dependents(
                project_id,
                None if kinds is None else frozenset(kinds),
            )
        except DomainError as exc:
            _LOG.warning("no code graph for project %s: %s", project_id, exc)
            return None, {}

    def handover(  # noqa: PLR0913, PLR0917 -- MCP_TOOLS.md fixes these fields
        self,
        ctx: TabContext,
        action: str,
        goal: str | None,
        done: list[str],
        decisions: list[str],
        open_questions: list[str],
        next_steps: list[str],
        failing_checks: list[str],
    ) -> HandoverResult:
        _require_tab(ctx)
        with self._lock:
            if action == "write":
                return self._write_handover(ctx, goal, done, decisions, open_questions, next_steps, failing_checks)
            return self._read_handover(ctx)

    def list_plans(self, ctx: TabContext, status: str) -> PlanList:
        _require_tab(ctx)
        with self._lock:
            rows = [
                _row(stored)
                for stored in self._plans.values()
                if stored.project_id == ctx.project_id and (status == "all" or _active(stored))
            ]
            rows.sort(key=lambda item: item.updated_at, reverse=True)
            return PlanList(plans=rows)

    def _insert(self, stored: _Stored, nodes: list[NodeIn]) -> list[NodeRef]:
        fresh = [item for item in nodes if item.key not in stored.meta]
        if not fresh:
            return []
        try:
            topology = topology_for(self._state(stored)).add(tuple(topology_contract(item) for item in fresh))
        except DomainError as exc:
            _domain(exc)
        cycle = self._preview(stored, fresh)
        if cycle is not None:
            _stop("PLAN_CYCLE", "cycle " + " -> ".join(cycle))
        now = self._now()
        stored.dag.add_many(tuple(_spec(spec, now) for spec in fresh))
        for spec in fresh:
            stored.deps[spec.key] = tuple(spec.depends_on)
            stored.meta[spec.key] = _meta(self._id("node"), spec)
        stored.updated_at = now
        stored.topology = topology
        return [NodeRef(key=spec.key, node_id=stored.meta[spec.key].node_id) for spec in fresh]

    def _preview(self, stored: _Stored, fresh: list[NodeIn]) -> tuple[str, ...] | None:
        _require_present(stored, fresh)
        scratch = _restored(self._state(stored)).dag
        try:
            scratch.add_many(tuple(_spec(spec, self._now()) for spec in fresh))
        except DomainError as exc:
            if exc.name == "PLAN_CYCLE":
                return exc.extra
            _domain(exc)
        return None

    def _ctx(self, ctx: TabContext, *, node_id: str | None, query: str | None, max_items: int) -> CtxResult:
        paths: tuple[str, ...] = ()
        if node_id is not None:
            _stored, meta = self._node(ctx, node_id)
            paths = meta.paths
        needle = "" if query is None else query.casefold()
        matched = [
            item for item in self._facts.values() if item.project_id == ctx.project_id and _hit(item, needle, paths)
        ]
        matched.sort(key=lambda item: (item.stale, item.title))
        chosen = matched[:max_items]
        return CtxResult(
            items=[_item(fact) for fact in chosen],
            truncated=len(matched) > max_items,
            verified_commit=_verified(ctx.worktree),
        )

    def _write_handover(  # noqa: PLR0913, PLR0917 -- the handover packet is one write
        self,
        ctx: TabContext,
        goal: str | None,
        done: list[str],
        decisions: list[str],
        open_questions: list[str],
        next_steps: list[str],
        failing_checks: list[str],
    ) -> HandoverResult:
        if goal is None or goal.strip() == "":
            _stop("NODE_EVIDENCE_REQUIRED", "handover write needs a goal")
        for kb_id in decisions:
            fact = self._facts.get(kb_id)
            if fact is None or fact.project_id != ctx.project_id:
                _stop("NOTE_NOT_FOUND", f"decision {kb_id} is not a knowledge item")
        handover_id = self._id("ho")
        note_ref = f"aconsole://note/{handover_id}"
        packet = _Handover(
            handover_id=handover_id,
            note_ref=note_ref,
            goal=goal,
            done=tuple(done),
            decisions=tuple(decisions),
            open_questions=tuple(open_questions),
            next_steps=tuple(next_steps),
            failing_checks=tuple(failing_checks),
        )
        if self._knowledge is not None:
            record = StoredHandover(
                id=handover_id,
                project_id=ctx.project_id,
                tab_id=ctx.tab_id,
                note_ref=note_ref,
                goal=goal,
                done=packet.done,
                decisions=packet.decisions,
                open_questions=packet.open_questions,
                next_steps=packet.next_steps,
                failing_checks=packet.failing_checks,
                created_at=self._now(),
            )
            try:
                self._knowledge.save_handover(record)
            except StoreWriteError as exc:
                _stop("INTERNAL_ERROR", str(exc))
        self._handovers[ctx.tab_id] = packet
        return HandoverResult(handover_id=handover_id, note_ref=note_ref, goal=goal)

    def _read_handover(self, ctx: TabContext) -> HandoverResult:
        packet = self._handovers.get(ctx.tab_id)
        nodes = [
            _brief(stored, meta, detailed=False)
            for stored in self._plans.values()
            if stored.project_id == ctx.project_id
            for meta in stored.meta.values()
            if stored.dag.get(meta.key).status not in {NodeStatus.DONE, NodeStatus.CANCELLED}
        ]
        diff_stat = _diff(ctx.worktree)
        if packet is None:
            return HandoverResult(diff_stat=diff_stat, nodes=nodes)
        return HandoverResult(
            handover_id=packet.handover_id,
            note_ref=packet.note_ref,
            goal=packet.goal,
            done=list(packet.done),
            decisions=list(packet.decisions),
            open_questions=list(packet.open_questions),
            next_steps=list(packet.next_steps),
            failing_checks=list(packet.failing_checks),
            diff_stat=diff_stat,
            nodes=nodes,
        )

    def _expire_checked(self, stored: _Stored) -> _Stored:
        """An abandoned verification returns to the queue; evidence never turns it green by itself."""
        if self._checked_completion and stored.dag.expire_leases(self._now()):
            self._commit(stored)
        return stored

    def _owned(self, ctx: TabContext, plan_id: str) -> _Stored:
        stored = self._plans.get(plan_id)
        if stored is None or stored.project_id != ctx.project_id:
            _stop("PLAN_NOT_FOUND", f"no plan {plan_id}")
        return self._expire_checked(stored)

    def _ready_plan(self, ctx: TabContext, plan_id: str | None) -> _Stored:
        if plan_id is not None:
            return self._owned(ctx, plan_id)
        for stored in self._plans.values():
            if stored.project_id != ctx.project_id:
                continue
            for meta in stored.meta.values():
                node = stored.dag.get(meta.key)
                if node.tab_id == ctx.tab_id and node.lease_id is not None:
                    return self._expire_checked(stored)
        for stored in reversed(tuple(self._plans.values())):
            if stored.project_id == ctx.project_id:
                return self._expire_checked(stored)
        return _stop("PLAN_NOT_FOUND", "this tab has no plan")

    def _node(self, ctx: TabContext, node_id: str) -> tuple[_Stored, _Meta]:
        for stored in self._plans.values():
            if stored.project_id != ctx.project_id:
                continue
            for meta in stored.meta.values():
                if meta.node_id == node_id:
                    return self._expire_checked(stored), meta
        return _stop("NODE_NOT_FOUND", f"no node {node_id}")

    def _by_title(self, project_id: str, title: str) -> _Stored | None:
        for stored in self._plans.values():
            if stored.project_id == project_id and stored.title == title:
                return stored
        return None

    def _new_plan(self, project_id: str, title: str, goal: str) -> _Stored:
        plan_id = self._id("plan")
        stored = _Stored(
            plan_id=plan_id,
            project_id=project_id,
            title=title,
            goal=goal,
            dag=Plan(checked_completion=self._checked_completion, lease_namespace=plan_id),
            meta={},
            deps={},
            updated_at=self._now(),
            created_at=self._now(),
        )
        self._plans[plan_id] = stored
        return stored

    def _id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    def _target(self, project_id: str, target: str) -> str | None:
        if target in self._facts and self._facts[target].project_id == project_id:
            return target
        for fact in self._facts.values():
            if fact.project_id == project_id and (target in fact.code_refs or fact.title == target):
                return fact.kb_id
        return None

    def _edges(self, project_id: str, kinds: list[str] | None) -> dict[str, tuple[str, ...]]:
        allowed = None if kinds is None else set(kinds)
        edges: dict[str, list[str]] = {}
        for fact in self._facts.values():
            if fact.project_id != project_id:
                continue
            if allowed is not None and fact.kind not in allowed:
                continue
            linked = list(fact.code_refs)
            edges.setdefault(fact.kb_id, []).extend(linked)
            for ref in fact.code_refs:
                edges.setdefault(ref, []).append(fact.kb_id)
        return {key: tuple(dict.fromkeys(values)) for key, values in edges.items()}


def _clock() -> datetime:
    return datetime.now(UTC)


def _actor_context(actor: PipelineActor) -> TabContext:
    return TabContext(
        tab_id=actor.tab_id, project_id=actor.project_id, worktree=actor.worktree, agent=actor.agent, mode=actor.mode
    )


def _number(identifier: str) -> int:
    """`node-12` gives 12. Ids count up across a restart from the largest one kept."""
    digits = identifier.rpartition("-")[2]
    return int(digits) if digits.isdigit() else 0


def _plan_status(stored: _Stored) -> Literal["active", "completed"]:
    finished = {NodeStatus.DONE, NodeStatus.CANCELLED}
    every = [stored.dag.get(key).status for key in stored.meta]
    return "completed" if every and all(status in finished for status in every) else "active"


def _busy(stored: _Stored) -> bool:
    for meta in stored.meta.values():
        node = stored.dag.get(meta.key)
        if node.lease_id is not None and node.status in _HELD:
            return True
    return False


def _gate_profile(raw: msgspec.Raw) -> dict[str, object] | None:
    try:
        return TypeAdapter(dict[str, object] | None).validate_json(bytes(raw))
    except ValueError as exc:
        _stop("NODE_CONTRACT", f"gate_profile must be a JSON object: {exc}")


def _mcp_specs(nodes: list[NodeIn]) -> tuple[PlanNodeSpec, ...]:
    return tuple(
        PlanNodeSpec(
            key=item.key,
            title=item.title,
            objective=item.objective,
            depends_on=tuple(item.depends_on),
            parent=item.parent,
            related=tuple(item.related),
            paths=tuple(item.paths),
            acceptance=tuple(item.acceptance),
            level=item.level,
            invariants=item.invariants,
            gate_profile=(
                msgspec.Raw(b"null")
                if item.gate_profile is None
                else msgspec.Raw(json.dumps(item.gate_profile, sort_keys=True, separators=(",", ":")).encode())
            ),
            test_cmd=item.test_cmd,
            review_required=item.review_required,
            output_format=item.output_format,
            boundaries=item.boundaries,
            owner_agent=item.owner_agent,
        )
        for item in nodes
    )


def _node_inputs(nodes: tuple[PlanNodeSpec, ...]) -> list[NodeIn]:
    try:
        return [
            NodeIn(
                key=item.key,
                title=item.title,
                objective=item.objective,
                depends_on=list(item.depends_on),
                parent=item.parent,
                related=list(item.related),
                owner_agent=item.owner_agent,
                paths=list(item.paths),
                acceptance=list(item.acceptance),
                level=item.level,
                invariants=item.invariants,
                gate_profile=_gate_profile(item.gate_profile),
                test_cmd=item.test_cmd,
                review_required=item.review_required,
                output_format=item.output_format,
                boundaries=item.boundaries,
            )
            for item in nodes
        ]
    except pydantic_core.ValidationError as exc:
        _stop("NODE_CONTRACT", str(exc))


def _stored_node(stored: _Stored, meta: _Meta, layer: int, now: datetime) -> StoredNode:
    node = stored.dag.get(meta.key)
    return StoredNode(
        id=meta.node_id,
        key=meta.key,
        title=meta.title,
        objective=meta.objective,
        status=node.status.value,
        owner=meta.owner,
        paths=meta.paths,
        acceptance=meta.acceptance,
        review_required=node.review_required,
        boundaries=meta.boundaries,
        output_format=meta.output_format,
        evidence=node.evidence,
        note=node.note,
        fence=node.fence,
        tab_id=node.tab_id,
        lease_id=node.lease_id,
        lease_expires_at=node.lease_expires_at,
        layer=layer,
        priority=stored.dag.priority(meta.key, now),
        unmet_blockers=len(stored.dag.unmet_blockers(meta.key)),
        created_at=node.created_at,
        depends_on=stored.deps.get(meta.key, ()),
        parent=meta.parent,
        related=meta.related,
        test_cmd=meta.test_cmd,
        max_attempts=meta.max_attempts,
        contract_sha256=meta.contract_sha256,
        blast_radius=meta.blast_radius,
        baseline_children=meta.baseline_children,
        level=node.level,
        pipeline=node.pipeline,
        invariants=node.invariants,
        gate_profile=None if node.gate_profile is None else dict(node.gate_profile),
        artifacts=tuple(
            StoredArtifact(id=artifact_id, kind=kind, ref=ref) for (kind, ref), artifact_id in meta.artifacts.items()
        ),
    )


def _node_note(stored: _Stored, meta: _Meta, graph: Owners) -> NodeNote:
    node = stored.dag.get(meta.key)
    note = VaultNote(
        status=node.status.value,
        owner_agent=meta.owner or "",
        depends_on=stored.deps.get(meta.key, ()),
        paths=meta.paths,
        acceptance=meta.acceptance,
        review_required=node.review_required,
        tags=("aconsole/plan",),
        lease="" if node.lease_expires_at is None else node.lease_expires_at.isoformat(),
        updated=stored.updated_at.isoformat(),
        title=meta.title,
        objective=meta.objective,
    )
    owners = replace(graph, node_key=meta.key, leased_by_other=node.lease_expires_at is not None)
    return NodeNote(key=meta.key, note=note, owners=owners, node_id=meta.node_id, level=node.level, parent=meta.parent)


def _after_restart(item: StoredNode) -> NodeStatus:
    """A held node whose lease row is gone ends as an expired lease does: it can be claimed again."""
    status = NodeStatus(item.status)
    if item.pipeline and status is NodeStatus.VERIFYING:
        return status
    if status in {NodeStatus.IN_PROGRESS, NodeStatus.VERIFYING} and item.lease_expires_at is None:
        return NodeStatus.PENDING
    return status


def _restored(saved: StoredPlan, *, checked_completion: bool = False) -> _Stored:
    dag = Plan(checked_completion=checked_completion, lease_namespace=saved.id)
    meta: dict[str, _Meta] = {}
    deps: dict[str, tuple[str, ...]] = {}
    for item in saved.nodes:
        dag.restore(
            Node(
                key=item.key,
                created_at=item.created_at,
                status=_after_restart(item),
                paths=item.paths,
                level=item.level,
                pipeline=item.pipeline,
                invariants=item.invariants,
                gate_profile=item.gate_profile,
                review_required=item.review_required,
                tab_id=item.tab_id,
                lease_id=item.lease_id,
                fence=item.fence,
                lease_expires_at=item.lease_expires_at,
                evidence=item.evidence,
                note=item.note,
            ),
            depends_on=item.depends_on,
            parent=item.parent,
            related=item.related,
        )
        deps[item.key] = item.depends_on
        meta[item.key] = _Meta(
            node_id=item.id,
            key=item.key,
            title=item.title,
            objective=item.objective,
            acceptance=item.acceptance,
            paths=item.paths,
            owner=item.owner,
            boundaries=item.boundaries,
            output_format=item.output_format,
            parent=item.parent,
            related=item.related,
            test_cmd=item.test_cmd,
            max_attempts=item.max_attempts,
            contract_sha256=item.contract_sha256,
            blast_radius=item.blast_radius,
            baseline_children=item.baseline_children,
            artifacts={(artifact.kind, artifact.ref): artifact.id for artifact in item.artifacts},
        )
    return _Stored(
        plan_id=saved.id,
        project_id=saved.project_id,
        title=saved.title,
        goal=saved.goal,
        dag=dag,
        meta=meta,
        deps=deps,
        updated_at=saved.updated_at,
        created_at=saved.created_at,
        topology=saved.topology,
    )


def _require_tab(ctx: TabContext) -> None:
    if ctx.tab_id == "":
        _stop("INGEST_UNAUTHORIZED", "the request has no tab token")


def _planning_nodes(ctx: TabContext, nodes: list[NodeIn]) -> list[NodeIn]:
    if ctx.mode == "execute":
        _stop("PLAN_ROLE", "graph changes require a planning tab")
    if ctx.mode is None:
        return nodes
    for item in nodes:
        if item.level == 0:
            try:
                _ = micro_commands(item.gate_profile or {}, location=f"{item.key}.gate_profile")
            except DomainError as exc:
                _domain(exc)
        direction_ok = (
            bool(item.invariants and item.gate_profile) if item.level == 0 else bool(item.parent and item.test_cmd)
        )
        if not (item.objective.strip() and item.paths and item.acceptance and direction_ok):
            _stop("NODE_CONTRACT", f"incomplete planning contract: {item.key}")
    return [item.model_copy(update={"pipeline": item.level == 1}) for item in nodes]


def _domain(exc: DomainError) -> NoReturn:
    detail = exc.name if not exc.extra else exc.name + " " + ", ".join(exc.extra)
    _stop(exc.name, detail)


def _domain_error(exc: _ToolError) -> DomainError:
    wire = TypeAdapter(dict[str, object]).validate_json(exc.result.model_dump_json(by_alias=True))
    body = TypeAdapter(dict[str, object]).validate_python(wire.get("structuredContent", {}))
    error = TypeAdapter(dict[str, object]).validate_python(body.get("error", {}))
    name = str(error.get("reason", "NODE_CONTRACT"))
    code = error.get("code", 4013)
    detail = str(error.get("message", name))
    return DomainError(
        code=code if isinstance(code, int) else 4013, name=name, retryable=name == "LEASE_HELD", extra=(detail,)
    )


def _stop(name: str, message: str) -> NoReturn:
    hint = _HINTS.get(name, "call plan_get and retry the last legal step")
    text = f"{name}: {message}"
    result = CallToolResult(
        content=[TextContent(type="text", text=f"{text}. Next: {hint}")],
        is_error=True,
        structured_content={"error": {"code": _CODES[name], "reason": name, "message": text, "hint": hint}},
    )
    raise _ToolError(result)


def _require_present(stored: _Stored, fresh: list[NodeIn]) -> None:
    known = set(stored.deps)
    incoming = {spec.key for spec in fresh}
    for spec in fresh:
        _known(spec.depends_on, known, incoming, "depends_on")
        _known(spec.related, known, incoming, "related")
        if spec.parent is not None:
            _known((spec.parent,), known, incoming, "parent")


def _known(keys: tuple[str, ...] | list[str], known: set[str], incoming: set[str], label: str) -> None:
    for key in keys:
        if key not in known and key not in incoming:
            _stop("NODE_NOT_FOUND", f"{label} {key} is not a node")


def _spec(spec: NodeIn, created_at: datetime) -> NodeSpec:
    return NodeSpec(
        key=spec.key,
        created_at=created_at,
        depends_on=tuple(spec.depends_on),
        parent=spec.parent,
        related=tuple(spec.related),
        paths=tuple(spec.paths),
        level=spec.level,
        pipeline=spec.pipeline,
        invariants=spec.invariants,
        gate_profile=spec.gate_profile,
        review_required=spec.review_required,
    )


def _meta(node_id: str, spec: NodeIn) -> _Meta:
    return _Meta(
        node_id=node_id,
        key=spec.key,
        title=spec.title,
        objective=spec.objective,
        acceptance=tuple(item[:300] for item in spec.acceptance),
        paths=tuple(spec.paths),
        owner=spec.owner_agent,
        boundaries=spec.boundaries,
        output_format=spec.output_format,
        parent=spec.parent,
        related=tuple(spec.related),
        test_cmd=spec.test_cmd,
    )


def _refs(stored: _Stored) -> list[NodeRef]:
    return [NodeRef(key=meta.key, node_id=meta.node_id) for meta in stored.meta.values()]


def _canvas(plan_id: str) -> str:
    return f"aconsole://plan/{plan_id}"


def _counts(stored: _Stored) -> dict[str, int]:
    counts = {status.value: 0 for status in NodeStatus}
    for meta in stored.meta.values():
        counts[stored.dag.get(meta.key).status.value] += 1
    counts["total"] = len(stored.meta)
    return counts


def _pipeline_contract_ready(stored: _Stored, meta: _Meta) -> bool:
    return bool(meta.contract_sha256 and stored.topology is not None and stored.topology.phase == "frozen")


def _execution_ready(stored: _Stored, meta: _Meta) -> bool:
    node = stored.dag.get(meta.key)
    return (
        stored.dag.ready(meta.key)
        and (not node.pipeline or _pipeline_contract_ready(stored, meta))
        and _profile_error(stored, meta) is None
    )


def _profile_error(stored: _Stored, meta: _Meta) -> str | None:
    node = stored.dag.get(meta.key)
    parent = stored.meta.get(meta.parent or "")
    direction = meta if node.level == 0 else parent if node.pipeline else None
    if direction is not None:
        try:
            _ = micro_commands(
                stored.dag.get(direction.key).gate_profile or {}, location=f"{direction.key}.gate_profile"
            )
        except DomainError as exc:
            return "; ".join(exc.extra)
    return None


def _brief(stored: _Stored, meta: _Meta, *, detailed: bool) -> NodeBrief:
    node = stored.dag.get(meta.key)
    brief = NodeBrief(
        node_id=meta.node_id,
        key=meta.key,
        title=meta.title,
        status=node.status.value,
        ready=_execution_ready(stored, meta),
        pipeline=node.pipeline,
        contract_sha256=meta.contract_sha256,
        contract_error=_profile_error(stored, meta),
        owner=meta.owner,
    )
    if not detailed:
        return brief
    return brief.model_copy(
        update={
            "depends_on": list(stored.deps[meta.key]),
            "objective": meta.objective,
            "acceptance": list(meta.acceptance),
            "paths": list(meta.paths),
            "lease_id": node.lease_id,
            "fence": node.fence,
            "expires_at": _optional_stamp(node.lease_expires_at),
        }
    )


def _row(stored: _Stored) -> PlanRow:
    done = sum(1 for meta in stored.meta.values() if stored.dag.get(meta.key).status is NodeStatus.DONE)
    return PlanRow(
        plan_id=stored.plan_id,
        title=stored.title,
        done=done,
        total=len(stored.meta),
        updated_at=stored.updated_at.isoformat(),
    )


def _active(stored: _Stored) -> bool:
    if not stored.meta:
        return True
    resting = {NodeStatus.DONE, NodeStatus.CANCELLED}
    return any(stored.dag.get(meta.key).status not in resting for meta in stored.meta.values())


def _hit(fact: _Fact, needle: str, paths: tuple[str, ...]) -> bool:
    if needle and needle not in fact.title.casefold() and needle not in fact.body.casefold():
        return False
    if paths and not set(paths).intersection(fact.code_refs):
        return needle != ""
    return True


def _item(fact: _Fact) -> CtxItem:
    summary = fact.body.replace("\n", " ").strip()
    return CtxItem(
        id=fact.kb_id,
        type=fact.kind,
        title=fact.title,
        summary=summary[:_SUMMARY],
        source=fact.source,
        note_ref=fact.note_ref,
        stale=fact.stale,
    )


def _check_source(ctx: TabContext, source: str) -> None:
    if _URL.fullmatch(source) is not None:
        return
    found = _LINE.fullmatch(source)
    if found is None or ctx.worktree is None:
        _stop("PATH_NOT_ALLOWED", "a fact source is path#Lx-Ly inside the worktree, or an http(s) URL")
    relative = Path(found.group(1))
    if relative.is_absolute() or ".." in relative.parts:
        _stop("PATH_NOT_ALLOWED", "a fact source must stay inside the worktree")
    path = ctx.worktree / relative
    if not path.is_file():
        _stop("PATH_NOT_ALLOWED", f"{relative.as_posix()} is not a file in the worktree")


def impact_layers(edges: dict[str, tuple[str, ...]], start: str, hops: int, limit: int) -> tuple[list[list[str]], bool]:
    seen = {start}
    frontier = [start]
    layers: list[list[str]] = []
    truncated = False
    for _hop in range(hops):
        nxt: list[str] = []
        for node in frontier:
            for linked in edges.get(node, ()):
                if linked in seen:
                    continue
                seen.add(linked)
                nxt.append(linked)
                if sum(len(layer) for layer in layers) + len(nxt) > limit:
                    truncated = True
                    nxt = nxt[: limit - sum(len(layer) for layer in layers)]
                    break
            if truncated:
                break
        if nxt:
            layers.append(nxt)
        frontier = nxt
        if truncated or not frontier:
            break
    return layers, truncated


def _stamp(moment: datetime | None) -> str:
    if moment is None:
        return ""
    return moment.isoformat()


def _optional_stamp(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return moment.isoformat()


def _diff(worktree: Path | None) -> str:
    return _git(worktree, "diff", "--stat", "--no-ext-diff", "--no-textconv")[:_DIFF]


def _verified(worktree: Path | None) -> str | None:
    text = _git(worktree, "rev-parse", "HEAD")
    return text or None


def _git(worktree: Path | None, *args: str) -> str:
    if worktree is None or not worktree.is_dir() or not (worktree / ".git").exists():
        return ""
    try:
        return _GIT_RUNNER.read(worktree, *args).text.strip()
    except GitCommandError:
        return ""


_CODES: dict[str, int] = {
    "GATE_RUNNER_UNAVAILABLE": 5010,
    "ATTEMPTS_EXHAUSTED": 4016,
    "STEP_OUT_OF_ORDER": 4017,
    "GATE_RECEIPT_REQUIRED": 4018,
    "PLAN_GROWTH_LIMIT": 4014,
    "SPLIT_NO_PROOF": 4019,
    "PLAN_FROZEN": 4011,
    "PLAN_ROLE": 4012,
    "NODE_CONTRACT": 4013,
    "RESPONSE_TOO_LARGE": -32000,
    "INTERNAL_ERROR": -32603,
    "INGEST_UNAUTHORIZED": 1001,
    "PATH_NOT_ALLOWED": 2011,
    "PLAN_NOT_FOUND": 4001,
    "NODE_NOT_FOUND": 4002,
    "PLAN_CYCLE": 4003,
    "NODE_TRANSITION_INVALID": 4004,
    "NODE_NOT_READY": 4005,
    "LEASE_HELD": 4006,
    "LEASE_STALE_FENCE": 4007,
    "LEASE_EXPIRED": 4008,
    "NODE_EVIDENCE_REQUIRED": 4009,
    "PATHS_OVERLAP": 4010,
    "SCOPE_VIOLATION": 4015,
    "NOTE_NOT_FOUND": 5005,
    "IMPACT_TARGET_UNKNOWN": 5007,
}

_HINTS: dict[str, str] = {
    "NODE_CONTRACT": (
        "inspect plan_get phase and contract_error; correct the named gate_profile fields in the plan contract; "
        "Freeze applies only to a draft, and unblocking does not repair a profile; "
        "legacy nodes use node_update instead of step_begin"
    ),
    "RESPONSE_TOO_LARGE": "use concise output, a revision, or a narrower query; no answer was truncated",
    "INTERNAL_ERROR": "nothing was saved; retry, and read the core log if it keeps failing",
    "INGEST_UNAUTHORIZED": "open a tab and pass Authorization: Bearer <tab token>",
    "PATH_NOT_ALLOWED": "call kb_add with source path#Lx-Ly that exists in the worktree, or an https URL",
    "PLAN_NOT_FOUND": "call plan_list to see plans for this project",
    "NODE_NOT_FOUND": "call plan_get to see node ids",
    "PLAN_CYCLE": "call plan_get and drop the depends_on edge that closes the cycle",
    "NODE_TRANSITION_INVALID": "call plan_get, then node_update with an allowed status",
    "NODE_NOT_READY": "call plan_ready to get claimable nodes",
    "LEASE_HELD": "call plan_ready for a node this tab can claim",
    "LEASE_STALE_FENCE": "call plan_get for the current fence, then retry node_update",
    "LEASE_EXPIRED": "call node_claim again",
    "NODE_EVIDENCE_REQUIRED": "call node_update with evidence, or handover write with a goal",
    "PATHS_OVERLAP": "call plan_ready for a node whose paths do not overlap an in-progress node",
    "NOTE_NOT_FOUND": "call kb_add before referencing that kb id",
    "IMPACT_TARGET_UNKNOWN": "call ctx_get to find a known target",
}
