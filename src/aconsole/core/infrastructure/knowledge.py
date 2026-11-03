"""`plan/*` and `knowledge/*` on the wire. The plan board stays the one owner of plans."""

from __future__ import annotations

from typing import Final, final

import msgspec

from aconsole.contracts.app import (
    NoteUriParams,
    NoteUriResult,
    PlanIdParams,
    PlanListParams,
    PlanListResult,
    SetNodeStatusParams,
)
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.mcp.tools import PlanBoard
from aconsole.core.infrastructure.vault.sync import VaultSync

PLAN_METHODS: Final = frozenset({"plan/list", "plan/get", "plan/setNodeStatus"})
KNOWLEDGE_METHODS: Final = PLAN_METHODS | {"knowledge/noteUri"}
_PLAN_NOT_FOUND: Final = 4001
_NOTE_NOT_FOUND: Final = 5005


@final
class KnowledgeService:
    def __init__(self, board: PlanBoard, vault: VaultSync) -> None:
        self.board: PlanBoard = board
        self.vault: VaultSync = vault

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
        if method == "knowledge/noteUri":
            return self._note_uri(msgspec.json.decode(raw, type=NoteUriParams))
        moved = msgspec.json.decode(raw, type=SetNodeStatusParams)
        return self.board.set_node_status(str(moved.node_id), moved.status.value, moved.note)

    def vault_ok(self) -> bool:
        return self.vault.root is not None

    def _note_uri(self, params: NoteUriParams) -> NoteUriResult:
        path = self.vault.resolve(str(params.note))
        if path is None:
            raise DomainError(code=_NOTE_NOT_FOUND, name="NOTE_NOT_FOUND", retryable=False, extra=(str(params.note),))
        return NoteUriResult(uri=path.as_uri())
