"""Plans reach the snapshot and the plan methods, and survive a core restart."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aconsole.contracts.app import (
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
from tests.core.test_wire import _connect, _daemon, _git_repo, _stop

if TYPE_CHECKING:
    from pathlib import Path


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
            "plan/setNodeStatus", SetNodeStatusParams(node_id=second.id, status=NodeStatus.CANCELLED), PlanNodeView,
        )
        assert moved.status is NodeStatus.CANCELLED
        assert await client.error(
            "plan/setNodeStatus", SetNodeStatusParams(node_id=NodeId("node-99"), status=NodeStatus.DONE),
        ) == "NODE_NOT_FOUND"
        assert await client.error(
            "plan/setNodeStatus", SetNodeStatusParams(node_id=second.id, status=NodeStatus.DONE),
        ) == "NODE_TRANSITION_INVALID"
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
            _context(project), str(plan.id),
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
