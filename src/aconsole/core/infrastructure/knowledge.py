"""`plan/*` and `knowledge/*` on the wire. The plan board stays the one owner of plans."""

from __future__ import annotations

from typing import Final, final

import msgspec
from pydantic import TypeAdapter

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
    PlanSaveParams,
    SetNodeStatusParams,
)
from aconsole.contracts.common import NodeId, PlanId
from aconsole.core.application.ports.plan_store import StoredPlan
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.scope import check_scope
from aconsole.core.infrastructure.codegraph.index import CodeGraphIndex
from aconsole.core.infrastructure.mcp.tools import PlanBoard, impact_layers
from aconsole.core.infrastructure.persistence.repositories.code_graph import persistent_impact
from aconsole.core.infrastructure.vault.sync import VaultSync

PLAN_METHODS: Final = frozenset(
    {
        "plan/directionGate",
        "plan/inbox",
        "plan/decide",
        "plan/list",
        "plan/get",
        "plan/save",
        "plan/delete",
        "plan/setNodeStatus",
        "plan/freeze",
        "plan/applyChange",
    }
)
KNOWLEDGE_METHODS: Final = PLAN_METHODS | {"knowledge/noteUri", "knowledge/impact"}
_IMPACT_LIMIT: Final = 200
_IMPACT_UNKNOWN: Final = 5007
_PLAN_NOT_FOUND: Final = 4001
_NOTE_NOT_FOUND: Final = 5005


@final
class KnowledgeService:
    def __init__(self, board: PlanBoard, vault: VaultSync, code_graph: CodeGraphIndex) -> None:
        self.board: PlanBoard = board
        self.vault: VaultSync = vault
        self.code_graph: CodeGraphIndex = code_graph

    def dispatch(self, method: str, raw: bytes) -> msgspec.Struct:
        if method.startswith("plan/"):
            return self._plan(method, raw)
        if method == "knowledge/impact":
            return self._impact(msgspec.json.decode(raw, type=ImpactParams))
        return self._note_uri(msgspec.json.decode(raw, type=NoteUriParams))

    def _plan(self, method: str, raw: bytes) -> msgspec.Struct:
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
        return self._document(method, raw)

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
        return self.board.set_node_status(str(moved.node_id), moved.status.value, moved.note)

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
