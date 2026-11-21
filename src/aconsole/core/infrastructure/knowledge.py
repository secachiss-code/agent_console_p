"""`plan/*` and `knowledge/*` on the wire. The plan board stays the one owner of plans."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Final, Literal, final

import msgspec
from pydantic import BaseModel, TypeAdapter

from aconsole.contracts.app import (
    DirectionGateParams,
    DirectionGateResult,
    EmptyResult,
    ImpactParams,
    ImpactView,
    NoteUriParams,
    NoteUriResult,
    PlanApplyChangeParams,
    PlanDecideParams,
    PlanIdParams,
    PlanInboxEntry,
    PlanInboxResult,
    PlanListParams,
    PlanListResult,
    PlanNodeDetailsParams,
    PlanNodeDetailsView,
    PlanSaveParams,
    ProjectIndexParams,
    SetNodeStatusParams,
)
from aconsole.contracts.common import NodeId, PlanId
from aconsole.contracts.primitives import (
    GateRunParams,
    HandoverReadParams,
    HandoverView,
    HandoverWriteParams,
    KnowledgeAddParams,
    KnowledgeAddResult,
    KnowledgeGetParams,
    KnowledgeGetResult,
    NodeAttachParams,
    NodeAttachResult,
    NodeClaimParams,
    NodeClaimResult,
    NodeEscalateParams,
    NodeStepParams,
    NodeUpdateParams,
    NodeUpdateResult,
    PipelineStepResult,
    PlanAddNodeParams,
    PlanAddNodeResult,
    PlanCreateParams,
    PlanCreateResult,
    PlanReadyParams,
    PlanReadyResult,
)
from aconsole.core.application.ports.plan_store import StoredPlan
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.pipeline import Step
from aconsole.core.domain.scope import check_scope
from aconsole.core.infrastructure.codegraph.index import CodeGraphIndex, impact_layers
from aconsole.core.infrastructure.mcp.project_index import ProjectIndex
from aconsole.core.infrastructure.mcp.tools import (
    PipelineReply,
    PlanBoard,
    TabContext,
    as_domain_error,
    nodes_from_specs,
)
from aconsole.core.infrastructure.persistence.repositories.code_graph import persistent_impact
from aconsole.core.infrastructure.vault.sync import VaultSync

PLAN_METHODS: Final = frozenset(
    {
        "plan/directionGate",
        "plan/inbox",
        "plan/decide",
        "plan/list",
        "plan/get",
        "plan/nodeDetails",
        "plan/save",
        "plan/delete",
        "plan/setNodeStatus",
        "plan/freeze",
        "plan/applyChange",
        "plan/create",
        "plan/addNode",
        "plan/ready",
    }
)
KNOWLEDGE_METHODS: Final = PLAN_METHODS | {
    "project/index",
    "knowledge/noteUri",
    "knowledge/impact",
    "knowledge/get",
    "knowledge/add",
    "handover/read",
    "handover/write",
    "node/claim",
    "node/update",
    "node/attach",
    "node/step",
    "node/escalate",
    "gate/run",
}
_IMPACT_LIMIT: Final = 200
_IMPACT_UNKNOWN: Final = 5007
_PLAN_NOT_FOUND: Final = 4001
_NOTE_NOT_FOUND: Final = 5005
_INDEX_QUERY_MAX: Final = 300
_INDEX_PATH_MAX: Final = 4096


@final
class KnowledgeService:
    def __init__(self, board: PlanBoard, vault: VaultSync, code_graph: CodeGraphIndex) -> None:
        self.board: PlanBoard = board
        self.vault: VaultSync = vault
        self.code_graph: CodeGraphIndex = code_graph
        self.project_index: ProjectIndex = ProjectIndex(board)
        self.tab_context: Callable[[str], TabContext | None] = lambda _tab: None

    def dispatch(self, method: str, raw: bytes) -> msgspec.Struct:
        if method == "project/index":
            return self._project_index(msgspec.json.decode(raw, type=ProjectIndexParams))
        if method in {"plan/create", "plan/addNode", "plan/ready"}:
            return self._primitive(method, raw)
        if method.startswith("plan/"):
            return self._plan(method, raw)
        if method in {"knowledge/get", "knowledge/add"} or method.startswith(("handover/", "node/", "gate/")):
            return self._primitive(method, raw)
        if method == "knowledge/impact":
            return self._impact(msgspec.json.decode(raw, type=ImpactParams))
        return self._note_uri(msgspec.json.decode(raw, type=NoteUriParams))

    def _project_index(self, params: ProjectIndexParams) -> msgspec.Struct:
        if len(params.query) > _INDEX_QUERY_MAX or max(len(params.scope), len(params.anchor or "")) > _INDEX_PATH_MAX:
            raise ValueError
        if params.tab_id is None:
            ctx = _bind(None, str(params.project_id))
        else:
            context = self.tab_context(str(params.tab_id))
            if context is None or context.project_id != str(params.project_id):
                raise DomainError(code=2003, name="TAB_NOT_FOUND", retryable=False, extra=(str(params.tab_id),))
            ctx = context
        if ctx.worktree is None and self.board.project_root(ctx.project_id) is None:
            raise DomainError(code=2001, name="PROJECT_NOT_FOUND", retryable=False, extra=(ctx.project_id,))
        try:
            return self.project_index.scene(ctx, params)
        except Exception as exc:
            error = as_domain_error(exc)
            raise ValueError(error.name) from exc

    def _primitive(self, method: str, raw: bytes) -> msgspec.Struct:
        """One PlanBoard effect, with the ids from the request body."""
        return _as_wire(method, run_primitive(self.board, method, raw))

    def call_primitive(  # noqa: C901, PLR0911 -- one return per primitive, kept named for the branch
        self, method: str, raw: bytes, actor: TabContext | None = None
    ) -> BaseModel:
        if method == "plan/create":
            params = msgspec.json.decode(raw, type=PlanCreateParams)
            return self.board.create(_bind(actor, str(params.project_id)), params.title, params.goal, [])
        if method == "plan/addNode":
            params = msgspec.json.decode(raw, type=PlanAddNodeParams)
            return self.board.add_nodes(
                _bind(actor, str(params.project_id)), str(params.plan_id), nodes_from_specs((params.node,))
            )
        if method == "plan/ready":
            params = msgspec.json.decode(raw, type=PlanReadyParams)
            return self.board.ready(
                _bind(actor, str(params.project_id)),
                None if params.plan_id is None else str(params.plan_id),
                params.limit,
            )
        if method == "node/claim":
            params = msgspec.json.decode(raw, type=NodeClaimParams)
            return self.board.claim(
                _bind(actor, str(params.project_id), str(params.tab_id), "execute"), str(params.node_id)
            )
        if method == "node/update":
            params = msgspec.json.decode(raw, type=NodeUpdateParams)
            return self.board.update(
                _bind(actor, str(params.project_id), str(params.tab_id), "execute"),
                str(params.node_id),
                params.fence,
                params.status,
                params.note,
                params.evidence,
            )
        if method == "node/attach":
            params = msgspec.json.decode(raw, type=NodeAttachParams)
            return self.board.attach(
                _bind(actor, str(params.project_id), str(params.tab_id), "execute"),
                str(params.node_id),
                params.fence,
                params.kind,
                params.ref,
                params.title,
            )
        if method == "node/step":
            params = msgspec.json.decode(raw, type=NodeStepParams)
            return self.board.pipeline_step(
                _bind(actor, str(params.project_id), str(params.tab_id), "execute"),
                "reset" if params.step == "reset" else "submit",
                str(params.node_id),
                params.fence,
                step=_recorded_step(params.step),
                symbols=params.affected_symbols,
                note=params.note,
                patch=params.patch,
            )
        if method == "node/escalate":
            params = msgspec.json.decode(raw, type=NodeEscalateParams)
            return self.board.pipeline_step(
                _bind(actor, str(params.project_id), str(params.tab_id), "execute"),
                "escalate",
                str(params.node_id),
                params.fence,
                kind=params.kind,
                reason=params.reason,
            )
        if method == "gate/run":
            params = msgspec.json.decode(raw, type=GateRunParams)
            return self.board.pipeline_step(
                _bind(actor, str(params.project_id), str(params.tab_id), "execute"),
                "gate",
                str(params.node_id),
                params.fence,
                tier=params.tier,
            )
        if method == "knowledge/get":
            params = msgspec.json.decode(raw, type=KnowledgeGetParams)
            return self.board.ctx(
                _bind(actor, str(params.project_id)),
                None if params.node_id is None else str(params.node_id),
                params.query,
                params.max_items,
            )
        if method == "knowledge/add":
            params = msgspec.json.decode(raw, type=KnowledgeAddParams)
            return self.board.kb_add(
                _bind(actor, str(params.project_id)),
                params.kind,
                params.title,
                params.body,
                params.source,
                list(params.code_refs),
                params.supersedes,
            )
        if method == "handover/read":
            params = msgspec.json.decode(raw, type=HandoverReadParams)
            return self.board.handover(
                _bind(actor, str(params.project_id), str(params.tab_id)), "read", None, [], [], [], [], []
            )
        written = msgspec.json.decode(raw, type=HandoverWriteParams)
        return self.board.handover(
            _bind(actor, str(written.project_id), str(written.tab_id)),
            "write",
            written.goal,
            list(written.done),
            list(written.decisions),
            list(written.open_questions),
            list(written.next_steps),
            list(written.failing_checks),
        )

    def _plan(self, method: str, raw: bytes) -> msgspec.Struct:  # noqa: PLR0911 -- one result per plan method
        if method in {"plan/inbox", "plan/decide", "plan/directionGate"}:
            return self._review(method, raw)
        if method == "plan/list":
            listed = msgspec.json.decode(raw, type=PlanListParams)
            plans = self.board.views(str(listed.project_id), active_only=listed.status == "active")
            return PlanListResult(plans=plans)
        if method == "plan/get":
            wanted = msgspec.json.decode(raw, type=PlanIdParams)
            view = self.board.view(str(wanted.plan_id))
            if view is None:
                raise DomainError(code=_PLAN_NOT_FOUND, name="PLAN_NOT_FOUND", retryable=False)
            return view
        if method == "plan/freeze":
            wanted = msgspec.json.decode(raw, type=PlanIdParams)
            return self._freeze(str(wanted.plan_id))
        if method == "plan/applyChange":
            change = msgspec.json.decode(raw, type=PlanApplyChangeParams)
            return self.board.apply_change(str(change.plan_id), change.change_id, self._radii)
        if method != "plan/nodeDetails":
            return self._document(method, raw)
        return self._node_details(msgspec.json.decode(raw, type=PlanNodeDetailsParams))

    def _node_details(self, params: PlanNodeDetailsParams) -> PlanNodeDetailsView:
        ctx = _bind(None, str(params.project_id))
        try:
            detail = self.board.node_details(
                ctx, str(params.node_id), ("contract", "relations", "attempts", "reviews", "artifacts"),
                40, params.cursor,
            )
            paths = self.board.node_paths(ctx, str(params.node_id))
        except Exception as error:
            refusal = as_domain_error(error)
            if refusal.name == "NODE_NOT_FOUND":
                raise refusal from error
            raise ValueError(refusal.name) from error
        return PlanNodeDetailsView(
            lines=tuple(
                f"{entry.field}: " + (entry.value if isinstance(entry.value, str)
                                      else json.dumps(entry.value, ensure_ascii=False))
                for entry in detail.entries
            ),
            paths=paths, cursor=detail.cursor,
        )

    def _review(self, method: str, raw: bytes) -> msgspec.Struct:
        if method == "plan/directionGate":
            params = msgspec.json.decode(raw, type=DirectionGateParams)
            state = self.board.export_plan(str(params.plan_id))
            if not any(node.id == str(params.node_id) and node.level == 0 for node in state.nodes):
                raise DomainError(code=4002, name="NODE_NOT_FOUND", retryable=False)
            if self.board.directions is None:
                raise DomainError(code=5010, name="GATE_RUNNER_UNAVAILABLE", retryable=True)
            receipt = self.board.directions.run(str(params.node_id), str(params.tab_id), params.ui_pid)
            metrics = receipt.metrics
            return DirectionGateResult(
                passed=receipt.passed,
                receipt_hash=receipt.receipt_hash,
                verdict=receipt.verdict,
                core_rss_mib=metrics.core_rss_mib,
                ui_rss_mib=metrics.ui_rss_mib,
                tab_count=metrics.tab_count,
                cache_read_fraction=metrics.cache_read_fraction,
                nodes_before=metrics.nodes_before,
                nodes_after=metrics.nodes_after,
            )
        if method == "plan/decide":
            decision = msgspec.json.decode(raw, type=PlanDecideParams)
            if decision.decision == "apply":
                return self.board.apply_change(str(decision.plan_id), decision.request_id, self._radii)
            return self.board.decide(str(decision.plan_id), decision.request_id, decision.decision, decision.note)
        wanted = msgspec.json.decode(raw, type=PlanIdParams)
        plan = self.board.export_plan(str(wanted.plan_id))
        return PlanInboxResult(
            entries=tuple(
                PlanInboxEntry(
                    id=entry.id,
                    plan_id=PlanId(entry.plan_id),
                    node_id=None if entry.node_id is None else NodeId(entry.node_id),
                    kind=entry.kind,
                    body=entry.body,
                    status=entry.status,
                    payload_json=msgspec.json.encode(entry.payload).decode(),
                    created_at=entry.created_at or plan.created_at,
                    decided_at=entry.decided_at,
                    decided_by=entry.decided_by,
                )
                for entry in self.board.inbox(plan.id)
            )
        )

    def _document(self, method: str, raw: bytes) -> msgspec.Struct:
        if method == "plan/save":
            return self._save(raw)
        if method == "plan/delete":
            wanted = msgspec.json.decode(raw, type=PlanIdParams)
            self.board.delete_plan(str(wanted.plan_id))
            return EmptyResult()
        moved = msgspec.json.decode(raw, type=SetNodeStatusParams)
        return self.board.set_node_status(
            str(moved.node_id), moved.status.value, moved.note, accepted_debt=moved.accepted_debt,
        )

    def _save(self, raw: bytes) -> msgspec.Struct:
        try:
            params = msgspec.json.decode(raw, type=PlanSaveParams)
        except msgspec.ValidationError as exc:
            raise ValueError(str(exc)) from exc
        return self.board.save_document(
            str(params.project_id),
            params.title,
            params.goal,
            None if params.plan_id is None else str(params.plan_id),
            params.nodes,
            repair_paths=params.repair_paths,
            repair_contracts=params.repair_contracts,
            repair_note=params.repair_note,
            repair_profiles=None
            if params.repair_gate_profiles is None
            else {
                key: TypeAdapter(dict[str, object]).validate_json(bytes(profile))
                for key, profile in params.repair_gate_profiles.items()
            },
            allow_direction_repair=True,
        )

    def vault_ok(self) -> bool:
        return self.vault.root is not None

    def _freeze(self, plan_id: str) -> msgspec.Struct:
        plan = self.board.export_plan(plan_id)
        if plan.topology is not None and plan.topology.phase == "frozen":
            return self.board.freeze(plan_id, {}, graph_present=False)
        radii, present = self._radii(plan)
        return self.board.freeze(plan_id, radii, graph_present=present)

    def _radii(self, plan: StoredPlan) -> tuple[dict[str, tuple[str, ...] | None], bool]:
        graph = self.code_graph.graph(plan.project_id)
        present = bool(graph.nodes)
        paths = {node.id: node.path for node in graph.nodes}
        radii: dict[str, tuple[str, ...] | None] = {}
        for contract in plan.nodes:
            if contract.level != 1:
                continue
            radius: set[str] = set()
            for node in graph.nodes:
                if check_scope(contract.paths, (node.path,)).violations:
                    continue
                result = persistent_impact(self.code_graph, plan.project_id, node.id, 3, 10000, None)
                if result is None or result[1]:
                    raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False, extra=(contract.key,))
                radius.update(paths[module] for layer in result[0] for module in layer)
            radii[contract.key] = tuple(sorted(radius)) if present else None
        return radii, present

    def _impact(self, params: ImpactParams) -> ImpactView:
        project_id = str(params.project_id)
        start = self.code_graph.resolve(project_id, params.target)
        if start is None:
            raise DomainError(code=_IMPACT_UNKNOWN, name="IMPACT_TARGET_UNKNOWN", retryable=False)
        edges = self.code_graph.dependents(project_id, params.kinds)
        layers, truncated = impact_layers(edges, start, params.hops, _IMPACT_LIMIT)
        return ImpactView(
            target=start,
            hops=params.hops,
            layers=tuple(tuple(layer) for layer in layers),
            truncated=truncated,
            canvas=None,
        )

    def _note_uri(self, params: NoteUriParams) -> NoteUriResult:
        path = self.vault.resolve(str(params.note))
        if path is None:
            raise DomainError(code=_NOTE_NOT_FOUND, name="NOTE_NOT_FOUND", retryable=False, extra=(str(params.note),))
        return NoteUriResult(uri=path.as_uri())


_WIRE: dict[str, type[msgspec.Struct]] = {
    "plan/create": PlanCreateResult,
    "plan/addNode": PlanAddNodeResult,
    "plan/ready": PlanReadyResult,
    "node/claim": NodeClaimResult,
    "node/update": NodeUpdateResult,
    "node/attach": NodeAttachResult,
    "knowledge/get": KnowledgeGetResult,
    "knowledge/add": KnowledgeAddResult,
    "handover/read": HandoverView,
    "handover/write": HandoverView,
}


def run_primitive(
    board: PlanBoard, method: str, raw: bytes, ctx: TabContext | None = None, *, translate: bool = True
) -> BaseModel:
    """The board effect behind one REST method. MCP passes the live tab and keeps tool errors."""
    holder = KnowledgeService.__new__(KnowledgeService)
    holder.board = board
    try:
        return holder.call_primitive(method, raw, ctx)
    except Exception as exc:
        if not translate:
            raise
        raise as_domain_error(exc) from exc


def _bind(
    given: TabContext | None,
    project_id: str,
    tab_id: str = "human",
    mode: Literal["plan", "execute"] = "plan",
) -> TabContext:
    if given is not None:
        return given
    return _actor(project_id, tab_id, mode)


def _as_wire(method: str, model: BaseModel) -> msgspec.Struct:
    if isinstance(model, PipelineReply):
        return PipelineStepResult(reply_json=model.model_dump_json())
    return _wire(model, _WIRE[method])


def _actor(project_id: str, tab_id: str = "human", mode: Literal["plan", "execute"] = "plan") -> TabContext:
    """The caller of a primitive. MCP uses the same board methods with the tab's own context."""
    return TabContext(tab_id=tab_id, project_id=project_id, worktree=None, agent="human", mode=mode)


def _wire[T: msgspec.Struct](model: BaseModel, struct: type[T]) -> T:
    return msgspec.json.decode(model.model_dump_json().encode(), type=struct)


def _recorded_step(step: str) -> Step:
    if step == "patch":
        return "patch"
    if step == "test":
        return "test"
    if step == "verify":
        return "verify"
    return "spec"
