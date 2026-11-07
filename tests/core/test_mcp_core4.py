"""Cross-plan leases, truthful persistence, bounded MCP and dependency visibility."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from mcp import Client

from aconsole.contracts.app import ProjectAddParams, ProjectView, TabView
from aconsole.core.infrastructure.mcp.tools import NodeIn, PlanBoard, TabContext
from aconsole.core.infrastructure.persistence.repositories.plans import PlanDatabase
from tests.core.test_mcp_tools import _call, _Clock, _node, _server
from tests.core.test_wire import _connect, _daemon, _git_repo, _open, _stop

if TYPE_CHECKING:
    from pathlib import Path


def test_same_key_claims_in_two_persisted_plans_survive_reload(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            tab = await client.call("tab/open", _open(project.id), TabView)
            context = TabContext(tab_id=str(tab.id), project_id=str(project.id), worktree=None, agent="claude")
            board = daemon._plans
            ids = []
            leases = []
            for title in ("First", "Second"):
                made = board.create(context, title, "Ship", [NodeIn(key="N-1", title="Work", objective="done")])
                ident = made.nodes[0].node_id
                ids.append(ident)
                leases.append(board.claim(context, ident).lease_id)
            assert len(set(leases)) == 2
            writer = daemon._writer
            assert writer is not None
            fresh = PlanBoard()
            fresh.use_store(PlanDatabase(writer))
            for ident in ids:
                moved = fresh.update(context, ident, 1, None, None, None)
                assert moved.status == "in_progress"
            again = PlanBoard()
            again.use_store(PlanDatabase(writer))
            assert {view.nodes[0].status.value for view in again.views()} == {"in_progress"}
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_detailed_plan_get_explains_dependencies_and_delta() -> None:
    async def scenario() -> None:
        _board, server = _server(_Clock(), "tab")
        async with Client(server) as client:
            made = await _call(
                client,
                "plan_create",
                {
                    "title": "Dependencies",
                    "goal": "Ship",
                    "nodes": [_node("N-1"), _node("N-2", depends_on=["N-1"])],
                },
            )
            args = {"plan_id": made["plan_id"], "response_format": "detailed"}
            got = await _call(client, "plan_get", args)
            nodes = got["nodes"]
            assert isinstance(nodes, list)
            assert nodes[1]["depends_on"] == ["N-1"]
            assert nodes[1]["ready"] is False
            delta = await _call(client, "plan_get", {**args, "since": got["revision"]})
            assert delta.get("nodes", []) == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("plan_create", {"title": "x" * 121, "goal": "g"}),
        ("plan_create", {"title": "Plan", "goal": "x" * 8001}),
        ("plan_create", {"title": "Plan", "goal": "g", "nodes": [_node("N-1")] * 101}),
        ("plan_create", {"title": "Plan", "goal": "g", "nodes": [{**_node("N-1"), "paths": ["x"] * 101}]}),
        ("plan_create", {"title": "Plan", "goal": "g", "nodes": [{**_node("N-1"), "acceptance": ["x" * 2001]}]}),
        ("kb_add", {"type": "decision", "title": "x" * 301, "body": "b", "source": "s"}),
        ("kb_add", {"type": "decision", "title": "t", "body": "b", "source": "s", "code_refs": ["x"] * 101}),
        ("ctx_get", {"query": "x" * 2001}),
        ("node_attach", {"node_id": "n", "fence": 1, "kind": "file", "ref": "x" * 2001}),
        ("handover", {"action": "write", "goal": "g", "done": ["x"] * 101}),
    ],
)
def test_mcp_rejects_oversized_inputs(name: str, arguments: dict[str, object]) -> None:
    async def scenario() -> None:
        board, server = _server(_Clock(), "tab")
        async with Client(server) as client:
            answer = await client.call_tool(name, arguments)
            assert answer.is_error is True
            assert "NODE_NOT_FOUND" not in str(answer)
            assert board.views() == ()
            assert board._facts == {}
            assert board._handovers == {}

    asyncio.run(scenario())


def test_large_mcp_response_is_explicitly_refused_without_truncation() -> None:
    async def scenario() -> None:
        board, server = _server(_Clock(), "tab")
        context = TabContext(tab_id="tab", project_id="proj", worktree=None, agent="claude")
        made = board.create(
            context,
            "Large",
            "Ship",
            [NodeIn(key=f"N-{index}", title="Node", objective="я" * 2000) for index in range(100)],
        )
        async with Client(server) as client:
            answer = await client.call_tool("plan_get", {"plan_id": made.plan_id, "response_format": "detailed"})
            assert answer.is_error is True
            assert "RESPONSE_TOO_LARGE" in str(answer)
            assert len(str(answer).encode()) < 4096
            concise = await _call(client, "plan_get", {"plan_id": made.plan_id})
            assert len(concise["nodes"]) == 100

    asyncio.run(scenario())
