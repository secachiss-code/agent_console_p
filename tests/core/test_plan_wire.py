"""Plans reach the snapshot and the plan methods, and survive a core restart."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from aconsole.contracts.app import (
    PlanApplyChangeParams,
    PlanIdParams,
    PlanListParams,
    PlanListResult,
    PlanNodeView,
    PlanView,
    ProjectAddParams,
    ProjectView,
    SetNodeStatusParams,
)
from aconsole.contracts.common import NodeId, NodeStatus, PlanId
from aconsole.core.infrastructure.mcp.tools import NodeIn, PlanBoard, TabContext
from aconsole.core.infrastructure.persistence.models import PlanInbox
from aconsole.core.infrastructure.persistence.repositories.plans import PlanDatabase
from tests.core.test_wire import _connect, _daemon, _git_repo, _stop

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


@pytest.mark.parametrize("with_graph", [False, True])
def test_human_freeze_change_and_restart_keep_topology(  # noqa: PLR0915 -- one freeze/apply/restart scenario
    tmp_path: Path,
    *,
    with_graph: bool,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    if with_graph:
        (repo / "base.py").write_text("VALUE = 1\n")
        (repo / "dependent.py").write_text("import base\n")

    async def scenario() -> None:  # noqa: PLR0915 -- one freeze/apply/restart scenario
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        board = daemon._plans
        nodes = [
            NodeIn(
                key="D-0",
                title="Direction",
                objective="Domain",
                level=0,
                paths=["**"],
                invariants="pure",
                gate_profile={"tests": ["pytest"]},
            )
        ]
        nodes.extend(
            NodeIn(
                key=f"C-{i}",
                title=f"Module {i}",
                objective="Check",
                paths=["base.py"],
                acceptance=["green"],
                test_cmd="pytest",
                pipeline=True,
                parent="D-0",
            )
            for i in range(1, 5)
        )
        made = board.create(_context(project), "Freeze me", "Goal", nodes)
        params = PlanIdParams(plan_id=PlanId(made.plan_id))
        frozen = await client.call("plan/freeze", params, PlanView)
        assert frozen.phase == "frozen"
        assert frozen.warnings == (() if with_graph else ("NO_CODE_GRAPH",))
        state = board.export_plan(made.plan_id)
        assert state.topology is not None
        assert state.topology.baseline_nodes == 5
        assert all(node.contract_sha256 and len(node.contract_sha256) == 64 for node in state.nodes)
        module = state.nodes[1]
        assert module.blast_radius == (("dependent.py",) if with_graph else None)
        repeated = await client.call("plan/freeze", params, PlanView)
        assert repeated.updated_at == frozen.updated_at
        assert board.export_plan(made.plan_id).topology == state.topology
        assert not {tool.name for tool in await daemon._mcp.list_tools()}.intersection(
            {"plan_freeze", "plan_applyChange"}
        )
        writer = daemon._writer
        assert writer is not None

        def request_change(session: Session) -> None:
            audit = session.query(PlanInbox).filter_by(plan_id=made.plan_id, kind="human").all()
            assert len(audit) == 1
            assert audit[0].decided_by == "human"
            session.add(
                PlanInbox(
                    id="change",
                    plan_id=made.plan_id,
                    kind="add",
                    body="Add a module",
                    status="open",
                    payload={
                        "nodes": [
                            {
                                "key": "C-5",
                                "title": "New module",
                                "objective": "Check",
                                "paths": ["base.py"],
                                "acceptance": ["green"],
                                "test_cmd": "pytest",
                                "parent": "D-0",
                            }
                        ]
                    },
                    created_at=datetime.now(UTC),
                )
            )

        writer.call(request_change)
        change = PlanApplyChangeParams(plan_id=params.plan_id, change_id="change")
        applied = await client.call("plan/applyChange", change, PlanView)
        assert len(applied.nodes) == 6
        assert (await client.call("plan/applyChange", change, PlanView)).updated_at == applied.updated_at
        stored_change = PlanDatabase(writer).load_change("change")
        assert stored_change is not None
        assert stored_change.status == "applied"

        def invalid_changes(session: Session) -> None:
            session.add(
                PlanInbox(
                    id="split",
                    plan_id=made.plan_id,
                    kind="split",
                    body="Forged split",
                    status="open",
                    payload={"proof_receipt": "agent-forged-value", "source_node_id": state.nodes[1].id},
                    created_at=datetime.now(UTC),
                )
            )
            session.add(
                PlanInbox(
                    id="growth",
                    plan_id=made.plan_id,
                    kind="add",
                    body="Exceed baseline",
                    status="open",
                    payload={
                        "nodes": [
                            {
                                "key": "C-6",
                                "title": "Too many",
                                "objective": "Check",
                                "paths": ["base.py"],
                                "acceptance": ["green"],
                                "test_cmd": "pytest",
                                "parent": "D-0",
                            }
                        ]
                    },
                    created_at=datetime.now(UTC),
                )
            )

        writer.call(invalid_changes)
        for request, error in (("split", "SPLIT_NO_PROOF"), ("growth", "PLAN_GROWTH_LIMIT")):
            assert (
                await client.error("plan/applyChange", PlanApplyChangeParams(plan_id=params.plan_id, change_id=request))
                == error
            )
            pending = PlanDatabase(writer).load_change(request)
            assert pending is not None
            assert pending.status == "open"
        assert len(board.export_plan(made.plan_id).nodes) == 6
        await _stop(daemon, task, client)

        again = _daemon(tmp_path, serve_ingest=False)
        restarted = asyncio.create_task(again.serve())
        second = await _connect(again.socket)
        restored = await second.call("plan/get", params, PlanView)
        assert restored.phase == "frozen"
        kept = again._plans.export_plan(made.plan_id)
        assert kept.topology is not None
        assert kept.topology.gross_added == 6
        assert kept.topology.baseline_nodes == 5
        assert kept.topology.applied_changes == ("change",)
        assert all(node.contract_sha256 for node in kept.nodes)
        assert (await second.call("plan/applyChange", change, PlanView)).updated_at == restored.updated_at
        await _stop(again, restarted, second)

    asyncio.run(scenario())


def _context(project: ProjectView) -> TabContext:
    return TabContext(tab_id="t1", project_id=str(project.id), worktree=None, agent="claude")


def _nodes() -> list[NodeIn]:
    return [
        NodeIn(key="N-01", title="First", objective="do one", acceptance=["ok"], paths=["a/**"], boundaries="only a"),
        NodeIn(key="N-02", title="Second", objective="do two", depends_on=["N-01"]),
    ]


def test_plan_reaches_snapshot_and_survives_restart(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        board: PlanBoard = daemon._plans
        created = board.create(_context(project), "Ship it", "the goal", _nodes())
        claimed = board.claim(_context(project), created.nodes[0].node_id)
        assert claimed.fence == 1

        snapshot = await client.snapshot()
        assert [plan.title for plan in snapshot.plans] == ["Ship it"]
        plan = snapshot.plans[0]
        assert (plan.done, plan.total, plan.status) == (0, 2, "active")
        first, second = plan.nodes
        assert first.status is NodeStatus.IN_PROGRESS
        assert second.depends_on == (first.id,)
        assert not second.ready
        assert second.layer == 1

        listed = await client.call("plan/list", PlanListParams(project_id=project.id), PlanListResult)
        assert [item.id for item in listed.plans] == [plan.id]
        got = await client.call("plan/get", PlanIdParams(plan_id=plan.id), PlanView)
        assert got.goal == "the goal"
        assert await client.error("plan/get", PlanIdParams(plan_id=PlanId("plan-99"))) == "PLAN_NOT_FOUND"

        # A person cancels the second node; a bad move and an unknown node answer with their errors.
        moved = await client.call(
            "plan/setNodeStatus",
            SetNodeStatusParams(node_id=second.id, status=NodeStatus.CANCELLED),
            PlanNodeView,
        )
        assert moved.status is NodeStatus.CANCELLED
        assert (
            await client.error(
                "plan/setNodeStatus",
                SetNodeStatusParams(node_id=NodeId("node-99"), status=NodeStatus.DONE),
            )
            == "NODE_NOT_FOUND"
        )
        assert (
            await client.error(
                "plan/setNodeStatus",
                SetNodeStatusParams(node_id=second.id, status=NodeStatus.DONE),
            )
            == "NODE_TRANSITION_INVALID"
        )
        await _stop(daemon, task, client)

        again = _daemon(tmp_path, serve_ingest=False)
        restarted = asyncio.create_task(again.serve())
        second_client = await _connect(again.socket)
        kept = (await second_client.snapshot()).plans
        assert [item.id for item in kept] == [plan.id]
        assert [item.status for item in kept[0].nodes] == [NodeStatus.PENDING, NodeStatus.CANCELLED]
        assert kept[0].nodes[1].depends_on == (first.id,)
        # New ids carry on after the ones kept, so nothing collides.
        board_again: PlanBoard = again._plans
        added = board_again.add_nodes(
            _context(project),
            str(plan.id),
            [NodeIn(key="N-03", title="Third", objective="do three", depends_on=["N-01"])],
        )
        assert added.added[0].node_id not in {str(first.id), str(second.id)}
        details = board_again.get(_context(project), str(plan.id), detailed=True)
        assert details.nodes[0].objective == "do one"
        await _stop(again, restarted, second_client)

    asyncio.run(scenario())


def test_lease_of_a_gone_tab_does_not_stop_the_save(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        board: PlanBoard = daemon._plans
        created = board.create(_context(project), "Plan", "goal", _nodes())
        _ = board.claim(_context(project), created.nodes[0].node_id)
        late = board.create(_context(project), "Later", "goal", [])
        await _stop(daemon, task, client)
        again = _daemon(tmp_path, serve_ingest=False)
        restarted = asyncio.create_task(again.serve())
        reader = await _connect(again.socket)
        titles = {plan.title for plan in (await reader.snapshot()).plans}
        assert titles == {"Plan", "Later"}
        assert late.plan_id
        await _stop(again, restarted, reader)

    asyncio.run(scenario())
