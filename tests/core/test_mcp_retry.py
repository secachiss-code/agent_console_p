"""Released leases can be retried through MCP without weakening active leases."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from mcp import Client

from tests.core.test_mcp_tools import _call, _Clock, _error, _node, _node_id, _server


def test_mcp_retry_released_failed_node_without_fence() -> None:
    async def scenario() -> None:
        _board, server = _server(_Clock(), "tab-a")
        async with Client(server) as client:
            created = await _call(client, "plan_create", {
                "title": "Retry", "goal": "Ship", "nodes": [_node("A-1")],
            })
            node_id = _node_id(created, "A-1")
            first = await _call(client, "node_claim", {"node_id": node_id})
            failed = await _call(client, "node_update", {
                "node_id": node_id, "fence": first["fence"], "status": "failed", "note": "retry me",
            })
            assert failed["status"] == "failed"
            stale = await client.call_tool("node_update", {
                "node_id": node_id, "fence": first["fence"], "status": "pending",
            })
            assert stale.is_error is True
            assert _error(stale)["reason"] == "LEASE_STALE_FENCE"
            pending = await _call(client, "node_update", {"node_id": node_id, "status": "pending"})
            assert pending["status"] == "pending"
            second = await _call(client, "node_claim", {"node_id": node_id})
            assert first["fence"] == 1
            assert second["fence"] == 2
            rejected = await client.call_tool("node_update", {
                "node_id": node_id, "fence": first["fence"], "status": "pending",
            })
            assert rejected.is_error is True
            assert _error(rejected)["reason"] == "LEASE_STALE_FENCE"
            current = await _call(client, "node_update", {"node_id": node_id, "fence": second["fence"]})
            assert current["status"] == "in_progress"

    asyncio.run(scenario())


def test_mcp_retry_schema_allows_omitting_fence() -> None:
    async def scenario() -> None:
        _board, server = _server(_Clock(), "tab-a")
        async with Client(server) as client:
            listed = await client.list_tools()
            update = next(tool for tool in listed.tools if tool.name == "node_update")
            assert "fence" not in update.input_schema.get("required", [])
            assert "node_id" in update.input_schema["required"]

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [None, "pending"])
@pytest.mark.parametrize("include_fence", [False, True])
def test_mcp_retry_active_lease_rejects_missing_or_stale_fence(status: str | None, *, include_fence: bool) -> None:
    async def scenario() -> None:
        _board, server = _server(_Clock(), "tab-a")
        async with Client(server) as client:
            created = await _call(client, "plan_create", {
                "title": "Active", "goal": "Ship", "nodes": [_node("A-1")],
            })
            node_id = _node_id(created, "A-1")
            claimed = await _call(client, "node_claim", {"node_id": node_id})
            arguments: dict[str, object] = {"node_id": node_id}
            if include_fence:
                arguments["fence"] = 0
            if status is not None:
                arguments["status"] = status
            result = await client.call_tool("node_update", arguments)
            assert result.is_error is True
            assert _error(result)["reason"] == "LEASE_STALE_FENCE"
            current = await _call(client, "node_update", {"node_id": node_id, "fence": claimed["fence"]})
            assert current["status"] == "in_progress"

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [None, "pending"])
def test_mcp_retry_another_tab_cannot_update_active_lease(status: str | None) -> None:
    async def scenario() -> None:
        clock = _Clock()
        board, owner_server = _server(clock, "tab-a")
        _same, foreign_server = _server(clock, "tab-b", board=board)
        async with Client(owner_server) as owner, Client(foreign_server) as foreign:
            created = await _call(owner, "plan_create", {
                "title": "Owner", "goal": "Ship", "nodes": [_node("A-1")],
            })
            node_id = _node_id(created, "A-1")
            claimed = await _call(owner, "node_claim", {"node_id": node_id})
            arguments: dict[str, object] = {"node_id": node_id, "fence": claimed["fence"]}
            if status is not None:
                arguments["status"] = status
            result = await foreign.call_tool("node_update", arguments)
            assert result.is_error is True
            assert _error(result)["reason"] == "LEASE_HELD"
            current = await _call(owner, "node_update", {"node_id": node_id, "fence": claimed["fence"]})
            assert current["status"] == "in_progress"

    asyncio.run(scenario())


def test_mcp_retry_keeps_project_isolation() -> None:
    async def scenario() -> None:
        clock = _Clock()
        board, owner_server = _server(clock, "tab-a")
        _same, foreign_server = _server(clock, "tab-b", board=board, project_id="other")
        async with Client(owner_server) as owner, Client(foreign_server) as foreign:
            created = await _call(owner, "plan_create", {
                "title": "Project", "goal": "Ship", "nodes": [_node("A-1")],
            })
            node_id = _node_id(created, "A-1")
            claimed = await _call(owner, "node_claim", {"node_id": node_id})
            await _call(owner, "node_update", {
                "node_id": node_id, "fence": claimed["fence"], "status": "failed", "note": "retry",
            })
            result = await foreign.call_tool("node_update", {"node_id": node_id, "status": "pending"})
            assert result.is_error is True
            assert _error(result)["reason"] == "NODE_NOT_FOUND"
            pending = await _call(owner, "node_update", {"node_id": node_id, "status": "pending"})
            assert pending["status"] == "pending"

    asyncio.run(scenario())


def test_mcp_retry_verifying_lease_still_belongs_to_its_tab_after_deadline() -> None:
    async def scenario() -> None:
        clock = _Clock()
        board, owner_server = _server(clock, "tab-a")
        _same, foreign_server = _server(clock, "tab-b", board=board)
        async with Client(owner_server) as owner, Client(foreign_server) as foreign:
            created = await _call(owner, "plan_create", {
                "title": "Review", "goal": "Ship", "nodes": [_node("A-1")],
            })
            node_id = _node_id(created, "A-1")
            claimed = await _call(owner, "node_claim", {"node_id": node_id})
            await _call(owner, "node_update", {
                "node_id": node_id, "fence": claimed["fence"], "status": "verifying", "evidence": "tests passed",
            })
            clock.moment += timedelta(minutes=16)
            result = await foreign.call_tool("node_update", {
                "node_id": node_id, "fence": claimed["fence"], "status": "failed", "note": "foreign",
            })
            assert result.is_error is True
            assert _error(result)["reason"] == "LEASE_HELD"
            current = await _call(owner, "node_update", {"node_id": node_id, "fence": claimed["fence"]})
            assert current["status"] == "verifying"

    asyncio.run(scenario())
