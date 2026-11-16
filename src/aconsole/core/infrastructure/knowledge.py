"""`plan/*` and `knowledge/*` on the wire. The plan board stays the one owner of plans."""

from __future__ import annotations

from typing import Final, final

import msgspec

from aconsole.contracts.app import (
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
    {"plan/inbox", "plan/decide", "plan/list", "plan/get", "plan/setNodeStatus", "plan/freeze", "plan/applyChange"}
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
        if method in {"plan/inbox", "plan/decide"}:
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
        moved = msgspec.json.decode(raw, type=SetNodeStatusParams)
        return self.board.set_node_status(str(moved.node_id), moved.status.value, moved.note)

    def _review(self, method: str, raw: bytes) -> msgspec.Struct:
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
