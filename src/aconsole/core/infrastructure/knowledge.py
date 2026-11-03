"""`plan/*` and `knowledge/*` on the wire. The plan board stays the one owner of plans."""

from __future__ import annotations

from typing import Final, final

import msgspec

from aconsole.contracts.app import (
    ImpactParams,
    ImpactView,
    NoteUriParams,
    NoteUriResult,
    PlanIdParams,
    PlanListParams,
    PlanListResult,
    SetNodeStatusParams,
)
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.codegraph.index import CodeGraphIndex
from aconsole.core.infrastructure.mcp.tools import PlanBoard, impact_layers
from aconsole.core.infrastructure.vault.sync import VaultSync

PLAN_METHODS: Final = frozenset({"plan/list", "plan/get", "plan/setNodeStatus"})
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
        if method == "knowledge/impact":
            return self._impact(msgspec.json.decode(raw, type=ImpactParams))
        if method == "knowledge/noteUri":
            return self._note_uri(msgspec.json.decode(raw, type=NoteUriParams))
        moved = msgspec.json.decode(raw, type=SetNodeStatusParams)
        return self.board.set_node_status(str(moved.node_id), moved.status.value, moved.note)

    def vault_ok(self) -> bool:
        return self.vault.root is not None

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
