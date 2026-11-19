"""Reproduce the frozen plan-900 failure at admission and after durable reload."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
lazy from pathlib import Path

import msgspec
import pytest
from mcp import Client
lazy from sqlalchemy.orm import Session

from aconsole.core.application.ports.plan_store import StoreWriteError
from aconsole.core.application.services.plan_lifecycle import freeze_plan, repair_direction_profiles, topology_for
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
_BROKEN: dict[str, object] = {
    "direction": [[".venv-core/bin/python", "-m", "pytest", "-q"]],
    "environment": {"container": "devtools", "image": "sha256:" + "0" * 64},
}
_DIRECTION_PROFILE = {**MICRO_PROFILE, **_BROKEN}
_SEALED_PROFILE: dict[str, object] = {
    **MICRO_PROFILE,
    "direction": [["/opt/aconsole/core/bin/python", "-m", "pytest", "-q", "tests/core"]],
    "environment": {"container": "sealed-tier3", "image": "sha256:" + "a" * 64},
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
    made = board.create(
        _CTX,
        "Plan 900 regression",
        "Goal",
        [
            NodeIn(
                key="IDX-0",
                title="Index direction",
                objective="Index",
                level=0,
                paths=["src/**"],
                invariants="pure",
                gate_profile=_BROKEN,
            ),
            NodeIn(
                key="IDX-1",
                title="Merge languages",
                objective="Merge",
                parent="IDX-0",
                pipeline=True,
                paths=["src/a.py"],
                acceptance=["green"],
                test_cmd="pytest",
            ),
        ],
    )
    return board, board.export_plan(made.plan_id)


def test_incomplete_profile_cannot_be_frozen_or_change_draft_state() -> None:
    board, before = _broken_plan()
    with pytest.raises(DomainError) as error:
        freeze_plan(before, {}, at=_AT, graph_present=False)
    assert "IDX-0.gate_profile.micro missing commands: format, fix, lint, types, tests" in error.value.extra
    assert board.export_plan(before.id) == before


def test_direction_without_children_cannot_freeze_with_an_invalid_profile() -> None:
    _, draft = _broken_plan()
    standalone = replace(draft, nodes=draft.nodes[:1])
    with pytest.raises(DomainError) as error:
        freeze_plan(standalone, {}, at=_AT, graph_present=False)
    assert "IDX-0.gate_profile.micro missing commands" in error.value.extra[0]


@pytest.mark.parametrize("operation", ["plan_create", "plan_save"])
def test_planning_rejects_incomplete_profile_before_saving(operation: str) -> None:
    async def scenario() -> None:
        board = PlanBoard()
        planner = replace(_CTX, mode="plan")
        async with Client(build_server(board, planner)) as client:
            result = await client.call_tool(
                operation,
                {
                    "title": "Bad profile",
                    "goal": "Goal",
                    "nodes": [
                        {
                            "key": "IDX-0",
                            "title": "Index direction",
                            "objective": "Index",
                            "level": 0,
                            "paths": ["src/**"],
                            "acceptance": ["green"],
                            "invariants": "pure",
                            "gate_profile": _BROKEN,
                        },
                    ],
                },
            )
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
    saved = replace(
        draft, topology=topology, nodes=tuple(replace(node, contract_sha256=hashes[node.key]) for node in draft.nodes)
    )
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


def _legacy_frozen() -> StoredPlan:
    _, draft = _broken_plan()
    topology = topology_for(draft).freeze(at=_AT, decided_by="human")
    hashes = dict(topology.contract_hashes)
    return replace(
        draft,
        topology=topology,
        nodes=tuple(replace(node, contract_sha256=hashes[node.key], fence=2) for node in draft.nodes),
    )


def _executed_plan() -> StoredPlan:
    _, draft = _broken_plan()
    direction, module = draft.nodes
    ready = replace(draft, nodes=(replace(direction, gate_profile=_DIRECTION_PROFILE), module))
    frozen = freeze_plan(ready, {}, at=_AT, graph_present=False)
    return replace(frozen, nodes=(frozen.nodes[0], replace(frozen.nodes[1], status="done", fence=3)))


@pytest.mark.parametrize("case", ["l1_open", "l0_done", "lease", "micro_change", "invalid_image", "invalid_argv"])
def test_direction_repair_refuses_changes_outside_open_tier3(case: str) -> None:
    plan = _executed_plan()
    direction, module = plan.nodes
    profiles: dict[str, dict[str, object]] = {
        "micro_change": {**_SEALED_PROFILE, "micro": {**MICRO_PROFILE["micro"], "tests": ["false"]}},
        "invalid_image": {**_SEALED_PROFILE, "environment": _BROKEN["environment"]},
        "invalid_argv": {**_SEALED_PROFILE, "direction": [[]]},
    }
    if case == "l1_open":
        plan = replace(plan, nodes=(direction, replace(module, status="pending")))
    if case == "l0_done":
        plan = replace(plan, nodes=(replace(direction, status="done"), module))
    if case == "lease":
        plan = replace(plan, nodes=(replace(direction, lease_id="lease"), module))
    with pytest.raises(DomainError, match="LEASE_HELD" if case == "lease" else "NODE_CONTRACT"):
        _ = repair_direction_profiles(plan, {direction.key: profiles.get(case, _SEALED_PROFILE)}, at=_AT)


def _check_direction_amendment(before: StoredPlan, after: StoredPlan) -> None:
    assert before.topology is not None
    assert after.topology is not None
    assert after.topology.frozen_at == before.topology.frozen_at
    assert after.topology.revision_no == before.topology.revision_no + 1
    assert after.topology.gates_sha256 != before.topology.gates_sha256
    assert after.nodes[0].gate_profile == _SEALED_PROFILE
    assert after.nodes[0].contract_sha256 != before.nodes[0].contract_sha256
    assert after.nodes[1] == before.nodes[1]


def test_planning_repairs_legacy_profiles_and_preserves_identity_after_reload() -> None:
    async def scenario() -> None:
        saved = _legacy_frozen()
        store = _Memory()
        store.save(saved)
        board = PlanBoard(now=lambda: _AT)
        board.use_store(store)
        planner = replace(_CTX, mode="plan")
        async with Client(build_server(board, planner)) as client:
            params = {
                "plan_id": saved.id,
                "title": saved.title,
                "goal": saved.goal,
                "repair_gate_profiles": {"IDX-0": MICRO_PROFILE},
            }
            result = await client.call_tool("plan_save", params)
            assert not result.is_error, result
            repaired = board.export_plan(saved.id)
            assert repaired.topology is not None
            assert saved.topology is not None
            assert repaired.topology.revision_no == saved.topology.revision_no + 1
            assert repaired.topology.gates_sha256 != saved.topology.gates_sha256
            assert repaired.topology.baseline_nodes == saved.topology.baseline_nodes
            assert repaired.topology.gross_added == saved.topology.gross_added
            for before, after in zip(saved.nodes, repaired.nodes, strict=True):
                assert after == replace(before, gate_profile=after.gate_profile, contract_sha256=after.contract_sha256)
                assert after.contract_sha256 != before.contract_sha256
            assert not (await client.call_tool("plan_save", params)).is_error
            assert board.export_plan(saved.id) == repaired
        restarted = PlanBoard(now=lambda: _AT)
        restarted.use_store(store)
        assert restarted.export_plan(saved.id) == repaired
        assert [node.key for node in restarted.ready(_CTX, saved.id, 20).nodes] == ["IDX-1"]
        assert restarted.claim(_CTX, repaired.nodes[1].id).fence == 3

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["bad_argv", "unknown_key", "nodes", "rename", "no_id", "valid", "lease", "started"])
def test_profile_repair_refusals_leave_memory_and_store_unchanged(case: str) -> None:
    saved = _legacy_frozen()
    if case == "valid":
        saved = replace(saved, nodes=(replace(saved.nodes[0], gate_profile=MICRO_PROFILE), saved.nodes[1]))
    if case in {"lease", "started"}:
        module = (
            replace(saved.nodes[1], lease_id="lease") if case == "lease" else replace(saved.nodes[1], status="done")
        )
        saved = replace(saved, nodes=(saved.nodes[0], module))
    store = _Memory()
    store.save(saved)
    board = PlanBoard(now=lambda: _AT)
    board.use_store(store)
    profile = {**MICRO_PROFILE, "direction": [["pytest"]]}
    profiles = {"IDX-0": profile}
    if case == "bad_argv":
        profiles = {"IDX-0": _BROKEN}
    if case == "unknown_key":
        profiles = {"NO-0": profile}
    before = board.export_plan(saved.id)
    with pytest.raises(DomainError):
        board.save_document(
            saved.project_id,
            "Changed" if case == "rename" else saved.title,
            saved.goal,
            None if case == "no_id" else saved.id,
            () if case == "nodes" else None,
            repair_profiles=profiles,
        )
    assert board.export_plan(saved.id) == before
    assert store.plans[saved.id] == saved


def test_profile_repair_store_failure_rolls_back_memory() -> None:
    class FailingStore(_Memory):
        def save(self, plan: StoredPlan) -> None:
            message = f"disk full: {plan.id}"
            raise StoreWriteError(message)

    saved = _legacy_frozen()
    store = FailingStore()
    store.plans[saved.id] = saved
    board = PlanBoard(now=lambda: _AT)
    board.use_store(store)
    before = board.export_plan(saved.id)
    with pytest.raises(DomainError, match="INTERNAL_ERROR"):
        board.save_document(
            saved.project_id, saved.title, saved.goal, saved.id, None, repair_profiles={"IDX-0": MICRO_PROFILE}
        )
    assert board.export_plan(saved.id) == before
    assert store.plans[saved.id] == saved


@pytest.mark.parametrize("case", ["legacy", "legacy_attempt", "direction"])
def test_profile_repair_ipc_persists_audit_and_survives_restart(  # noqa: PLR0915 -- IPC, audit and restart
    tmp_path: Path, case: str
) -> None:
    from aconsole.contracts.app import PlanSaveParams, PlanView, ProjectAddParams, ProjectView
    from aconsole.contracts.common import PlanId
    from aconsole.core.infrastructure.persistence.models import GateReceipt, NodeAttempt, PlanInbox
    from aconsole.core.infrastructure.persistence.repositories.plans import PlanDatabase
    from tests.core.test_wire import _connect, _daemon, _git_repo, _stop

    repo = _git_repo(tmp_path / "repo")

    async def scenario() -> None:  # noqa: PLR0915 -- one daemon lifecycle per case
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        daemon._plans._now = lambda: _AT
        writer = daemon._writer
        assert writer is not None
        direction_repair = case == "direction"
        saved = replace(_executed_plan() if direction_repair else _legacy_frozen(), project_id=str(project.id))
        database = PlanDatabase(writer)
        database.save(saved)
        daemon._plans.use_store(database)
        profile: dict[str, object] = _SEALED_PROFILE if direction_repair else dict(MICRO_PROFILE)
        params = PlanSaveParams(
            project_id=project.id,
            title=saved.title,
            goal=saved.goal,
            plan_id=PlanId(saved.id),
            repair_gate_profiles={"IDX-0": msgspec.Raw(msgspec.json.encode(profile))},
        )
        if case != "legacy":

            def add_attempt(session: Session) -> None:
                session.add(
                    NodeAttempt(
                        id="old-attempt",
                        node_id=saved.nodes[1].id,
                        attempt_no=1,
                        status="passed" if direction_repair else "failed",
                        base_oid="a" * 40,
                        started_at=_AT,
                    )
                )
                if direction_repair:
                    session.add(
                        GateReceipt(
                            receipt_hash="b" * 64,
                            node_id=saved.nodes[1].id,
                            tier="blast",
                            state_hash="c" * 64,
                            argv_sha256="d" * 64,
                            exit_code=0,
                            passed=True,
                            compact_verdict="passed",
                            duration_ms=1,
                            created_at=_AT,
                        )
                    )

            writer.call(add_attempt)
        if case == "legacy_attempt":
            before = daemon._plans.export_plan(saved.id)
            assert await client.error("plan/save", params) == "NODE_CONTRACT"
            assert daemon._plans.export_plan(saved.id) == before
            await _stop(daemon, task, client)
            return
        before = daemon._plans.export_plan(saved.id)
        if direction_repair:
            planner = replace(_CTX, project_id=str(project.id), mode="plan")
            with pytest.raises(_ToolError, match="profile repair requires a plan without pipeline attempts"):
                _ = daemon._plans.save_for_tab(
                    planner, saved.title, saved.goal, saved.id, None, repair_profiles={"IDX-0": profile}
                )
        result = await client.call(
            "plan/save",
            params,
            PlanView,
        )
        assert result.phase == "frozen"
        repaired = daemon._plans.export_plan(saved.id)
        if direction_repair:
            _check_direction_amendment(before, repaired)
            assert (await client.call("plan/save", params, PlanView)).phase == "frozen"
            assert daemon._plans.export_plan(saved.id) == repaired

        def check_audit(session: Session) -> None:
            audit = session.query(PlanInbox).filter_by(plan_id=saved.id, kind="human").all()
            decision = "repair_direction_profiles" if direction_repair else "repair_gate_profiles"
            repairs = [entry for entry in audit if entry.payload.get("decision") == decision]
            assert len(repairs) == 1
            assert repairs[0].decided_by == "human"
            if direction_repair:
                attempt = session.get(NodeAttempt, "old-attempt")
                assert attempt is not None
                assert attempt.status == "passed"
                receipt = session.get(GateReceipt, "b" * 64)
                assert receipt is not None
                assert receipt.passed
                assert receipt.state_hash == "c" * 64

        writer.call(check_audit)
        await _stop(daemon, task, client)
        again = _daemon(tmp_path, serve_ingest=False)
        running = asyncio.create_task(again.serve())
        second = await _connect(again.socket)
        again._plans._now = lambda: _AT
        reloaded = again._plans.export_plan(saved.id)
        # Priority ages with wall time; durable contract and execution fields must match exactly.
        assert (
            replace(
                reloaded,
                nodes=tuple(
                    replace(node, priority=before.priority)
                    for node, before in zip(reloaded.nodes, repaired.nodes, strict=True)
                ),
            )
            == repaired
        )
        await _stop(again, running, second)

    asyncio.run(scenario())
