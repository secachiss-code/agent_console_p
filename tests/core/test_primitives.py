"""Primitive methods call the same plan board the agent tools use."""

import msgspec

from aconsole.contracts.common import PlanId, ProjectId
from aconsole.contracts.primitives import (
    KnowledgeGetParams,
    KnowledgeGetResult,
    PlanCreateParams,
    PlanCreateResult,
    PlanReadyParams,
    PlanReadyResult,
)
from aconsole.core.infrastructure.knowledge import KnowledgeService, run_primitive
from aconsole.core.infrastructure.mcp.tools import PlanBoard, PlanReady, TabContext


def test_create_add_and_ready_share_the_board() -> None:
    board = PlanBoard()
    service = KnowledgeService(board, _Unused(), _Unused())  # pyright: ignore[reportArgumentType]
    created = service.dispatch(
        "plan/create", _json(PlanCreateParams(project_id=ProjectId("p"), title="Build", goal="")),
    )
    assert isinstance(created, PlanCreateResult)
    assert created.nodes == ()
    ready = service.dispatch(
        "plan/ready",
        _json(PlanReadyParams(project_id=ProjectId("p"), plan_id=PlanId(created.plan_id))),
    )
    found = service.dispatch("knowledge/get", _json(KnowledgeGetParams(project_id=ProjectId("p"))))
    assert isinstance(ready, PlanReadyResult)
    assert ready.nodes == ()
    assert isinstance(found, KnowledgeGetResult)
    assert found.items == ()


def test_mcp_ready_keeps_the_live_tab() -> None:
    board = PlanBoard()
    seen: list[TabContext] = []

    def ready(ctx: TabContext, plan_id: str | None, limit: int) -> PlanReady:
        seen.append(ctx)
        _ = plan_id, limit
        return PlanReady(nodes=[])

    board.ready = ready  # pyright: ignore[reportAttributeAccessIssue]
    ctx = TabContext(tab_id="tab-1", project_id="p", worktree=None, agent="claude", mode="execute")
    found = run_primitive(board, "plan/ready", _json(PlanReadyParams(project_id=ProjectId("p"))), ctx)
    assert isinstance(found, PlanReady)
    assert seen[0].tab_id == "tab-1"
    assert seen[0].mode == "execute"


def _json(value: msgspec.Struct) -> bytes:
    return msgspec.json.encode(value)


class _Unused:
    """Stands in for vault and code graph. These calls do not touch them."""
