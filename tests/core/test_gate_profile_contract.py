"""Reproduce the frozen plan-900 failure at admission and after durable reload."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from mcp import Client

from aconsole.core.application.services.plan_lifecycle import freeze_plan, topology_for
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.gate_profile import MICRO_COMMANDS
from aconsole.core.infrastructure.mcp.server import build_server
from aconsole.core.infrastructure.mcp.tools import NodeIn, PlanBoard, TabContext, _ToolError
from aconsole.core.infrastructure.pipeline import micro_profile
from tests.core.test_mcp_tools import _error
from tests.core.test_plan_crud import _Memory
from tests.gate_profiles import MICRO_PROFILE
lazy from aconsole.core.application.ports.plan_store import StoredPlan

_AT = datetime(2026, 11, 16, tzinfo=UTC)
_CTX = TabContext(tab_id="executor", project_id="p", worktree=None, agent="codex")
_BROKEN = {
    "direction": [[".venv-core/bin/python", "-m", "pytest", "-q"]],
    "environment": {"container": "devtools", "image": "sha256:" + "0" * 64},
}


@pytest.mark.parametrize("command", MICRO_COMMANDS)
@pytest.mark.parametrize("bad", [None, [], "ruff check", [""], ["a\x00b"], [1], [" "]])
def test_bad_argv_identifies_the_command(command: str, bad: object) -> None:
    profile = {**MICRO_PROFILE["micro"], command: bad}
    with pytest.raises(DomainError) as error:
        micro_profile({"micro": profile}, location="IDX-0.gate_profile")
    assert error.value.name == "NODE_CONTRACT"
    assert f"IDX-0.gate_profile.micro.{command}" in error.value.extra[0]


def test_nested_and_legacy_flat_complete_profiles_are_equivalent() -> None:
    assert micro_profile(MICRO_PROFILE) == micro_profile(MICRO_PROFILE["micro"])


def _broken_plan() -> tuple[PlanBoard, StoredPlan]:
    board = PlanBoard(now=lambda: _AT)
    made = board.create(_CTX, "Plan 900 regression", "Goal", [
        NodeIn(key="IDX-0", title="Index direction", objective="Index", level=0, paths=["src/**"],
               invariants="pure", gate_profile=_BROKEN),
        NodeIn(key="IDX-1", title="Merge languages", objective="Merge", parent="IDX-0", pipeline=True,
               paths=["src/a.py"], acceptance=["green"], test_cmd="pytest"),
    ])
    return board, board.export_plan(made.plan_id)


def test_incomplete_profile_cannot_be_frozen_or_change_draft_state() -> None:
    board, before = _broken_plan()
    with pytest.raises(DomainError) as error:
        freeze_plan(before, {}, at=_AT, graph_present=False)
    assert "IDX-0.gate_profile.micro missing commands: format, fix, lint, types, tests" in error.value.extra
    assert board.export_plan(before.id) == before


@pytest.mark.parametrize("operation", ["plan_create", "plan_save"])
def test_planning_rejects_incomplete_profile_before_saving(operation: str) -> None:
    async def scenario() -> None:
        board = PlanBoard()
        planner = replace(_CTX, mode="plan")
        async with Client(build_server(board, planner)) as client:
            result = await client.call_tool(operation, {
                "title": "Bad profile", "goal": "Goal", "nodes": [
                    {"key": "IDX-0", "title": "Index direction", "objective": "Index", "level": 0,
                     "paths": ["src/**"], "acceptance": ["green"], "invariants": "pure", "gate_profile": _BROKEN},
                ],
            })
            error = _error(result)
            assert "IDX-0.gate_profile.micro missing commands" in str(error["message"])
            assert "unblocking does not repair" in str(error["hint"])
            assert board.views() == ()

    asyncio.run(scenario())


def test_old_frozen_profile_is_diagnosed_without_leasing_or_mutating_it() -> None:
    _board, draft = _broken_plan()
    # Build the exact persisted shape accepted by 0.1.3(a), bypassing only the new admission check.
    topology = topology_for(draft).freeze(at=_AT, decided_by="human")
    hashes = dict(topology.contract_hashes)
    saved = replace(draft, topology=topology, nodes=tuple(
        replace(node, contract_sha256=hashes[node.key]) for node in draft.nodes
    ))
    store = _Memory()
    store.save(saved)
    board = PlanBoard(now=lambda: _AT)
    board.use_store(store)
    module = saved.nodes[1]
    assert board.ready(_CTX, saved.id, 20).nodes == []
    assert board.view(saved.id).nodes[1].ready is False
    with pytest.raises(_ToolError) as error:
        board.claim(_CTX, module.id)
    assert "IDX-0.gate_profile.micro missing commands" in str(error.value)
    assert store.plans[saved.id] == saved
    got = board.get(_CTX, saved.id, detailed=True)
    assert got.phase == "frozen"
    assert got.nodes[1].contract_error == "IDX-0.gate_profile.micro missing commands: format, fix, lint, types, tests"
    # The execution parser emits the same specific error even for a lease already held by an old client.
    with pytest.raises(DomainError) as runtime_error:
        micro_profile(saved.nodes[0].gate_profile, location="IDX-0.gate_profile")
    assert got.nodes[1].contract_error in runtime_error.value.extra
