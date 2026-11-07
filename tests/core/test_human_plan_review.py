"""Human review is a single committed move; a failed write leaves verification intact."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import msgspec
import pytest
from mcp import Client

from aconsole.contracts.app import (
    PlanIdParams,
    PlanNodeView,
    PlanView,
    ProjectAddParams,
    ProjectView,
    SetNodeStatusParams,
)
from aconsole.contracts.common import NodeId, NodeStatus
from aconsole.core.infrastructure.mcp.tools import NodeIn
from aconsole.core.infrastructure.persistence.repositories import plans as plans_repo
from tests.core.test_mcp_tools import _call, _Clock, _error, _node, _node_id, _server
from tests.core.test_plan_store import _broken
from tests.core.test_plan_wire import _context
from tests.core.test_wire import _connect, _daemon, _git_repo, _stop

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("target", [NodeStatus.DONE, NodeStatus.PENDING])
def test_review_rpc_is_atomic_and_survives_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: NodeStatus,
) -> None:
    repo = _git_repo(tmp_path / "repo")

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            context = _context(project)
            made = daemon._plans.create(context, "Review", "goal", [
                NodeIn(key="A-1", title="Review me", objective="work", review_required=True, paths=["a.py"]),
            ])
            node_id = made.nodes[0].node_id
            claimed = daemon._plans.claim(context, node_id)
            daemon._plans.update(context, node_id, claimed.fence, "verifying", None, "Tests <passed>")
            before = daemon._plans.views()[0]
            node = before.nodes[0]
            assert node.evidence == "Tests <passed>"
            published = []
            original = daemon._plans.on_changed

            def observe(plan: PlanView) -> None:
                published.append(plan.nodes[0].status)
                if original is not None:
                    original(plan)

            daemon._plans.on_changed = observe
            params = SetNodeStatusParams(node_id=NodeId(node_id), status=target,
                note="Please revise" if target is NodeStatus.PENDING else None)
            with monkeypatch.context() as patch:
                patch.setattr(plans_repo, "save_plan", _broken)
                assert await client.error("plan/setNodeStatus", params) == "INTERNAL_ERROR"
            unchanged = await client.call("plan/get", PlanIdParams(plan_id=before.id), PlanView)
            assert msgspec.structs.replace(unchanged.nodes[0], priority=node.priority) == node
            assert not published
            moved = await client.call("plan/setNodeStatus", params, PlanNodeView)
            assert moved.status is target
            assert moved.tab_id is None
            assert moved.lease_expires_at is None
            assert published == [target], "no intermediate failed state is committed or broadcast"
        finally:
            await _stop(daemon, task, client)
        again = _daemon(tmp_path, serve_ingest=False)
        second = asyncio.create_task(again.serve())
        reader = await _connect(again.socket)
        try:
            restored = await reader.call("plan/get", PlanIdParams(plan_id=before.id), PlanView)
            assert restored.nodes[0].status is target
            stored, meta = again._plans._find_node(node_id)
            assert stored.dag.get(meta.key).evidence == "Tests <passed>"
        finally:
            await _stop(again, second, reader)

    asyncio.run(scenario())


def test_manual_policy_does_not_let_mcp_agent_accept_or_send_back_review() -> None:
    async def scenario() -> None:
        _board, server = _server(_Clock(), "tab-a")
        async with Client(server) as client:
            made = await _call(client, "plan_create", {
                "title": "Manual", "goal": "goal", "nodes": [{**_node("A-1"), "review_required": True}],
            })
            node_id = _node_id(made, "A-1")
            claimed = await _call(client, "node_claim", {"node_id": node_id})
            await _call(client, "node_update", {
                "node_id": node_id, "fence": claimed["fence"], "status": "verifying", "evidence": "tests pass",
            })
            for target in ("done", "pending"):
                rejected = await client.call_tool("node_update", {
                    "node_id": node_id, "fence": claimed["fence"], "status": target, "evidence": "tests pass",
                })
                assert rejected.is_error
                assert _error(rejected)["reason"] == "NODE_TRANSITION_INVALID"

    asyncio.run(scenario())
