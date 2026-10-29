"""The twelve MCP plan tools, backed by the domain plan DAG.

A repeated ``plan_create`` for the same project and title returns the same plan.
``node_update`` and ``node_attach`` require the fence from ``node_claim``.
"""

from __future__ import annotations

import inspect
import re
import subprocess
import threading
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal, NoReturn

from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, Field

from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.plan import Move, NodeSpec, NodeStatus, Plan, Role

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
            return fn(*args, **kwargs)
        except _ToolError as exc:
            return exc.result

    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    wrapped.__annotations__ = dict(fn.__annotations__)
    wrapped.__signature__ = signature
    return wrapped


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


class PlanBoard:
    """In-memory plans, knowledge, and handovers for one core process."""

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now: Callable[[], datetime] = now if now is not None else _clock
        self._lock: threading.RLock = threading.RLock()
        self._seq: int = 0
        self._plans: dict[str, _Stored] = {}
        self._facts: dict[str, _Fact] = {}
        self._handovers: dict[str, _Handover] = {}

    def create(self, ctx: TabContext, title: str, goal: str, nodes: list[NodeIn]) -> PlanCreated:
        """Create a plan, or return the one this project already has under ``title``."""
        _require_tab(ctx)
        with self._lock:
            stored = self._by_title(ctx.project_id, title)
            created = stored is None
            if stored is None:
                stored = self._new_plan(ctx.project_id, title, goal)
            try:
                _ = self._insert(stored, nodes)
            except _ToolError:
                if created:
                    del self._plans[stored.plan_id]
                raise
            return PlanCreated(plan_id=stored.plan_id, nodes=_refs(stored), canvas_note=_canvas(stored.plan_id))

    def add_nodes(self, ctx: TabContext, plan_id: str, nodes: list[NodeIn]) -> Added:
        """Add missing keys. A key that already exists is unchanged. A cycle adds nothing."""
        _require_tab(ctx)
        with self._lock:
            stored = self._owned(ctx, plan_id)
            added = self._insert(stored, nodes)
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
            stored.updated_at = self._now()
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
            stored.updated_at = now
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
            stored.updated_at = self._now()
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
                previous.stale = True
            kb_id = self._id("kb")
            note_ref = f"aconsole://note/{kb_id}"
            self._facts[kb_id] = _Fact(
                kb_id=kb_id,
                project_id=ctx.project_id,
                kind=kind,
                title=title,
                body=body,
                source=source,
                code_refs=tuple(code_refs),
                stale=False,
                note_ref=note_ref,
            )
            return KbAdded(kb_id=kb_id, note_ref=note_ref)

    def impact(self, ctx: TabContext, target: str, hops: int, limit: int, kinds: list[str] | None) -> Impact:
        _require_tab(ctx)
        with self._lock:
            start = self._target(ctx.project_id, target)
            if start is None:
                _stop("IMPACT_TARGET_UNKNOWN", f"no impact target {target}")
            layers, truncated = _layers(self._edges(ctx.project_id, kinds), start, hops, limit)
            note = "aconsole://note/impact" if truncated else ""
            return Impact(layers=layers, truncated=truncated, canvas_note=note)

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
        self._handovers[ctx.tab_id] = _Handover(
            handover_id=handover_id,
            note_ref=note_ref,
            goal=goal,
            done=tuple(done),
            decisions=tuple(decisions),
            open_questions=tuple(open_questions),
            next_steps=tuple(next_steps),
            failing_checks=tuple(failing_checks),
        )
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
        content=[TextContent(type="text", text=text)],
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


def _layers(edges: dict[str, tuple[str, ...]], start: str, hops: int, limit: int) -> tuple[list[list[str]], bool]:
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
