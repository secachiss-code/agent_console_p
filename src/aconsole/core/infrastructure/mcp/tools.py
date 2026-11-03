"""The twelve MCP plan tools, backed by the domain plan DAG.

A repeated ``plan_create`` for the same project and title returns the same plan.
``node_update`` and ``node_attach`` require the fence from ``node_claim``.
"""

from __future__ import annotations

import inspect
import logging
import re
import subprocess
import threading
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal, NoReturn, Protocol

import pydantic_core
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, Field

from aconsole.contracts.app import PlanNodeView, PlanView
from aconsole.contracts.common import AgentKind, NodeId, NoteRef, PlanId, ProjectId, TabId
from aconsole.contracts.common import NodeStatus as NodeStatusView
from aconsole.core.application.ports.knowledge_store import KnowledgeStore, StoredFact, StoredHandover
from aconsole.core.application.ports.plan_store import (
    PlanStore,
    StoredArtifact,
    StoredNode,
    StoredPlan,
    StoreWriteError,
)
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.plan import Move, Node, NodeSpec, NodeStatus, Plan, Role, after_merge
from aconsole.core.domain.vault_sync import Note as VaultNote
from aconsole.core.domain.vault_sync import Owners, resolve_ref
from aconsole.core.infrastructure.vault.sync import NodeNote

_LOG = logging.getLogger(__name__)
_NODE_NOT_FOUND = 4002
_HUMAN_TAB = "human"
_HELD = frozenset({NodeStatus.IN_PROGRESS, NodeStatus.VERIFYING})
_SUMMARY = 300
_DIFF = 4000
_LINE = re.compile(r"^(.+)#L(\d+)-(\d+)$")
_URL = re.compile(r"^https?://\S+$")


class _Frozen(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class NodeIn(_Frozen):
    """One node offered to ``plan_create`` or ``plan_add_nodes``."""

    key: str = Field(pattern=r"^[A-Z]{1,4}-\d{1,4}$")
    title: str = Field(min_length=3, max_length=120)
    objective: str = Field(max_length=2000)
    depends_on: list[str] = Field(default_factory=list)
    parent: str | None = None
    related: list[str] = Field(default_factory=list)
    owner_agent: Literal["claude", "codex"] | None = None
    paths: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    test_cmd: str | None = None
    review_required: bool = False
    output_format: str | None = None
    boundaries: str | None = None


class NodeRef(_Frozen):
    key: str
    node_id: str


class PlanCreated(_Frozen):
    plan_id: str
    nodes: list[NodeRef]
    canvas_note: str


class Added(_Frozen):
    added: list[NodeRef]
    unchanged: list[str]


class NodeBrief(_Frozen):
    node_id: str
    key: str
    title: str
    status: str
    ready: bool
    owner: str | None = None
    objective: str | None = None
    acceptance: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    lease_id: str | None = None
    fence: int | None = None
    expires_at: str | None = None


class PlanGot(_Frozen):
    title: str
    goal: str
    counts: dict[str, int]
    nodes: list[NodeBrief]


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


def publish(fn: Callable[..., object]) -> Callable[..., object]:
    """Keep the success return type as the tool schema, and surface ``_ToolError`` as the result."""
    signature = inspect.signature(fn, eval_str=True)

    def wrapped(*args: object, **kwargs: object) -> object:
        try:
            result = fn(*args, **kwargs)
        except _ToolError as exc:
            return exc.result
        return compact(result) if isinstance(result, BaseModel) else result

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
    return CallToolResult(content=[TextContent(type="text", text=pydantic_core.to_json(lean).decode())])


@dataclass(frozen=True, slots=True, kw_only=True)
class TabContext:
    """The tab a bearer token resolved. Tools never read the token itself."""

    tab_id: str
    project_id: str
    worktree: Path | None
    agent: str


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

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now: Callable[[], datetime] = now if now is not None else _clock
        self._lock: threading.RLock = threading.RLock()
        self._seq: int = 0
        self._plans: dict[str, _Stored] = {}
        self._facts: dict[str, _Fact] = {}
        self._handovers: dict[str, _Handover] = {}
        self._store: PlanStore | None = None
        self._saved: dict[str, StoredPlan] = {}
        self._knowledge: KnowledgeStore | None = None
        self.on_changed: Callable[[PlanView], None] | None = None
        self.note_ref: Callable[[str, str], str | None] = lambda _plan, _key: None
        self.code_graph: CodeGraph | None = None

    def use_store(self, store: PlanStore) -> None:
        """Take the plans `store` kept. From here on every change is saved there."""
        with self._lock:
            self._store = store
            for saved in store.load():
                self._plans[saved.id] = _restored(saved)
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

    def set_node_status(self, node_id: str, status: str, note: str | None) -> PlanNodeView:
        """The user moves a node. A human holds no fence, so the node's own fence is used. A note serves as evidence."""
        with self._lock:
            stored, meta = self._find_node(node_id)
            now = self._now()
            target = NodeStatus(status)
            _ = stored.dag.transition(
                Move(
                    key=meta.key,
                    to=target,
                    role=Role.HUMAN,
                    at=now,
                    tab_id=_HUMAN_TAB if target is NodeStatus.IN_PROGRESS else None,
                    fence=stored.dag.get(meta.key).fence if stored.dag.get(meta.key).lease_expires_at else None,
                    evidence=note,
                    note=note,
                )
            )
            self._commit(stored)
            view = self._view(stored)
            return next(item for item in view.nodes if str(item.id) == node_id)

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
            target = after_merge(node.status, review_required=node.review_required)
            evidence = f"merged as {oid}"
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
            return () if stored is None else tuple(_node_note(stored, meta) for meta in stored.meta.values())

    def apply_note(self, plan_id: str, key: str, note: VaultNote) -> None:
        """A person's edit the merge accepted. A status the rules refuse stays as it was, and the note follows."""
        with self._lock:
            stored = self._plans.get(plan_id)
            meta = None if stored is None else stored.meta.get(key)
            if stored is None or meta is None:
                return
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

    def _commit(self, stored: _Stored) -> None:
        """Every change goes through here: the store, then whoever shows plans."""
        stored.updated_at = self._now()
        if self._store is not None:
            state = self._state(stored)
            try:
                self._store.save(state)
            except StoreWriteError as exc:
                self._forget_unsaved(stored.plan_id)
                _stop("INTERNAL_ERROR", str(exc))
            self._saved[state.id] = state
        if self.on_changed is not None:
            self.on_changed(self._view(stored))

    def _forget_unsaved(self, plan_id: str) -> None:
        """Back to what the store holds, so that memory never promises more than a restart would keep."""
        saved = self._saved.get(plan_id)
        if saved is None:
            _ = self._plans.pop(plan_id, None)
        else:
            self._plans[plan_id] = _restored(saved)

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
        )

    def _view(self, stored: _Stored) -> PlanView:
        layers = {key: layer for key, layer, _position in stored.dag.layout()}
        now = self._now()
        nodes = tuple(self._node_view(stored, meta, layers.get(meta.key, 0), now) for meta in stored.meta.values())
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
            canvas=None,
            updated_at=stored.updated_at,
        )

    def _node_view(self, stored: _Stored, meta: _Meta, layer: int, now: datetime) -> PlanNodeView:
        node = stored.dag.get(meta.key)
        by_key = {item.key: item.node_id for item in stored.meta.values()}
        note = self.note_ref(stored.plan_id, meta.key)
        return PlanNodeView(
            id=NodeId(meta.node_id),
            key=meta.key,
            title=meta.title,
            status=NodeStatusView(node.status.value),
            ready=stored.dag.ready(meta.key),
            layer=layer,
            priority=stored.dag.priority(meta.key, now),
            depends_on=tuple(NodeId(by_key[key]) for key in stored.deps.get(meta.key, ()) if key in by_key),
            owner_agent=None if meta.owner is None else AgentKind(meta.owner),
            tab_id=None if node.tab_id is None or node.tab_id == _HUMAN_TAB else TabId(node.tab_id),
            lease_expires_at=node.lease_expires_at,
            note=None if note is None else NoteRef(note),
        )

    def create(self, ctx: TabContext, title: str, goal: str, nodes: list[NodeIn]) -> PlanCreated:
        """Create a plan, or return the one this project already has under ``title``."""
        _require_tab(ctx)
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

    def add_nodes(self, ctx: TabContext, plan_id: str, nodes: list[NodeIn]) -> Added:
        """Add missing keys. A key that already exists is unchanged. A cycle adds nothing."""
        _require_tab(ctx)
        with self._lock:
            stored = self._owned(ctx, plan_id)
            added = self._insert(stored, nodes)
            if added:
                self._commit(stored)
            fresh_keys = {ref.key for ref in added}
            unchanged = [item.key for item in nodes if item.key not in fresh_keys]
            return Added(added=added, unchanged=unchanged)

    def get(self, ctx: TabContext, plan_id: str, *, detailed: bool) -> PlanGot:
        _require_tab(ctx)
        with self._lock:
            stored = self._owned(ctx, plan_id)
            return PlanGot(
                title=stored.title,
                goal=stored.goal,
                counts=_counts(stored),
                nodes=[_brief(stored, meta, detailed=detailed) for meta in stored.meta.values()],
            )

    def ready(self, ctx: TabContext, plan_id: str | None, limit: int) -> PlanReady:
        _require_tab(ctx)
        with self._lock:
            stored = self._ready_plan(ctx, plan_id)
            ranked = [
                (stored.dag.priority(meta.key, self._now()), meta)
                for meta in stored.meta.values()
                if stored.dag.ready(meta.key)
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

    def update(  # noqa: PLR0913, PLR0917 -- status, note, and evidence travel with the fence
        self,
        ctx: TabContext,
        node_id: str,
        fence: int,
        status: str | None,
        note: str | None,
        evidence: str | None,
    ) -> Updated:
        _require_tab(ctx)
        with self._lock:
            stored, meta = self._node(ctx, node_id)
            now = self._now()
            try:
                if status is None:
                    node = stored.dag.heartbeat(meta.key, fence=fence, at=now)
                else:
                    node = stored.dag.transition(
                        Move(
                            key=meta.key,
                            to=NodeStatus(status),
                            role=Role.AGENT,
                            at=now,
                            tab_id=ctx.tab_id,
                            fence=fence,
                            note=note,
                            evidence=evidence,
                        )
                    )
            except DomainError as exc:
                _domain(exc)
            except ValueError:
                _stop("NODE_TRANSITION_INVALID", f"{status} is not a node status")
            self._commit(stored)
            return Updated(status=node.status.value, expires_at=_optional_stamp(node.lease_expires_at))

    def attach(  # noqa: PLR0913, PLR0917 -- kind and ref identify the artifact
        self, ctx: TabContext, node_id: str, fence: int, kind: str, ref: str, title: str | None
    ) -> Attached:
        _require_tab(ctx)
        with self._lock:
            stored, meta = self._node(ctx, node_id)
            try:
                _ = stored.dag.heartbeat(meta.key, fence=fence, at=self._now())
            except DomainError as exc:
                _domain(exc)
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
        self, project_id: str, target: str, kinds: list[str] | None,
    ) -> tuple[str | None, dict[str, tuple[str, ...]]]:
        """A failing extractor leaves the facts the board has. The tool's answer is still useful."""
        graph = self.code_graph
        if graph is None:
            return None, {}
        try:
            return graph.resolve(project_id, target), graph.dependents(
                project_id, None if kinds is None else frozenset(kinds),
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
        cycle = self._preview(stored, fresh)
        if cycle is not None:
            _stop("PLAN_CYCLE", "cycle " + " -> ".join(cycle))
        now = self._now()
        for spec in fresh:
            _ = stored.dag.add(
                NodeSpec(
                    key=spec.key,
                    created_at=now,
                    paths=tuple(spec.paths),
                    review_required=spec.review_required,
                )
            )
            stored.deps[spec.key] = ()
            stored.meta[spec.key] = _meta(self._id("node"), spec)
        for spec in fresh:
            for dep in spec.depends_on:
                stored.dag.depend(spec.key, dep)
            stored.deps[spec.key] = tuple(spec.depends_on)
        stored.updated_at = now
        return [NodeRef(key=spec.key, node_id=stored.meta[spec.key].node_id) for spec in fresh]

    def _preview(self, stored: _Stored, fresh: list[NodeIn]) -> tuple[str, ...] | None:
        _require_present(stored, fresh)
        scratch = Plan()
        for key in stored.dag.kahn() or ():
            node = stored.dag.get(key)
            _ = scratch.add(
                NodeSpec(
                    key=key,
                    created_at=node.created_at,
                    depends_on=stored.deps[key],
                    paths=node.paths,
                    review_required=node.review_required,
                )
            )
        for spec in fresh:
            _ = scratch.add(_spec(spec, self._now()))
        for spec in fresh:
            for dep in spec.depends_on:
                try:
                    scratch.depend(spec.key, dep)
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

    def _owned(self, ctx: TabContext, plan_id: str) -> _Stored:
        stored = self._plans.get(plan_id)
        if stored is None or stored.project_id != ctx.project_id:
            _stop("PLAN_NOT_FOUND", f"no plan {plan_id}")
        return stored

    def _ready_plan(self, ctx: TabContext, plan_id: str | None) -> _Stored:
        if plan_id is not None:
            return self._owned(ctx, plan_id)
        for stored in self._plans.values():
            if stored.project_id != ctx.project_id:
                continue
            for meta in stored.meta.values():
                node = stored.dag.get(meta.key)
                if node.tab_id == ctx.tab_id and node.lease_id is not None:
                    return stored
        for stored in reversed(tuple(self._plans.values())):
            if stored.project_id == ctx.project_id:
                return stored
        return _stop("PLAN_NOT_FOUND", "this tab has no plan")

    def _node(self, ctx: TabContext, node_id: str) -> tuple[_Stored, _Meta]:
        for stored in self._plans.values():
            if stored.project_id != ctx.project_id:
                continue
            for meta in stored.meta.values():
                if meta.node_id == node_id:
                    return stored, meta
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
            dag=Plan(),
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


def _number(identifier: str) -> int:
    """`node-12` gives 12. Ids count up across a restart from the largest one kept."""
    digits = identifier.rpartition("-")[2]
    return int(digits) if digits.isdigit() else 0


def _plan_status(stored: _Stored) -> Literal["active", "completed"]:
    finished = {NodeStatus.DONE, NodeStatus.CANCELLED}
    every = [stored.dag.get(key).status for key in stored.meta]
    return "completed" if every and all(status in finished for status in every) else "active"


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
        artifacts=tuple(
            StoredArtifact(id=artifact_id, kind=kind, ref=ref) for (kind, ref), artifact_id in meta.artifacts.items()
        ),
    )


def _node_note(stored: _Stored, meta: _Meta) -> NodeNote:
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
    owners = Owners(
        node_key=meta.key,
        known_keys=frozenset(stored.meta),
        edges=tuple((key, blocker) for key, blockers in stored.deps.items() for blocker in blockers),
        blocker_status=tuple((key, stored.dag.get(key).status.value) for key in stored.meta),
        leased_by_other=node.lease_expires_at is not None,
    )
    return NodeNote(key=meta.key, note=note, owners=owners)


def _after_restart(item: StoredNode) -> NodeStatus:
    """A held node whose lease row is gone ends as an expired lease does: it can be claimed again."""
    status = NodeStatus(item.status)
    if status in {NodeStatus.IN_PROGRESS, NodeStatus.VERIFYING} and item.lease_expires_at is None:
        return NodeStatus.PENDING
    return status


def _restored(saved: StoredPlan) -> _Stored:
    dag = Plan()
    meta: dict[str, _Meta] = {}
    deps: dict[str, tuple[str, ...]] = {}
    for item in saved.nodes:
        dag.restore(
            Node(
                key=item.key,
                created_at=item.created_at,
                status=_after_restart(item),
                paths=item.paths,
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
    )


def _require_tab(ctx: TabContext) -> None:
    if ctx.tab_id == "":
        _stop("INGEST_UNAUTHORIZED", "the request has no tab token")


def _domain(exc: DomainError) -> NoReturn:
    detail = exc.name if not exc.extra else exc.name + " " + ", ".join(exc.extra)
    _stop(exc.name, detail)


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
        paths=tuple(spec.paths),
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


def _brief(stored: _Stored, meta: _Meta, *, detailed: bool) -> NodeBrief:
    node = stored.dag.get(meta.key)
    brief = NodeBrief(
        node_id=meta.node_id,
        key=meta.key,
        title=meta.title,
        status=node.status.value,
        ready=stored.dag.ready(meta.key),
        owner=meta.owner,
    )
    if not detailed:
        return brief
    return brief.model_copy(
        update={
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
        completed = subprocess.run(  # noqa: S603 -- the argv is a list; git is not invoked through a shell
            ["git", "-C", str(worktree), *args],  # noqa: S607 -- git is resolved on PATH
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.decode("utf-8", errors="replace").strip()


_CODES: dict[str, int] = {
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
    "NOTE_NOT_FOUND": 5005,
    "IMPACT_TARGET_UNKNOWN": 5007,
}

_HINTS: dict[str, str] = {
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
