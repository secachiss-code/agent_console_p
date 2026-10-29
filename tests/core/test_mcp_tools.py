"""Each plan tool is called through ``Client(server)``: schema, content, and a hinted error."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from mcp import Client

from aconsole.core.infrastructure.mcp.server import build_server
from aconsole.core.infrastructure.mcp.tools import PlanBoard, TabContext

if TYPE_CHECKING:
    from pathlib import Path

    from mcp.server.mcpserver import MCPServer
    from mcp.types import CallToolResult, Tool

_HINT = "open a tab and pass Authorization: Bearer <tab token>"
_READ = frozenset({"plan_get", "plan_ready", "ctx_get", "kb_impact", "plan_list"})
_IDEMPOTENT = frozenset({"plan_create", "plan_add_nodes", "node_attach"})
_FIELDS = {
    "plan_create": {"plan_id", "nodes", "canvas_note"},
    "plan_add_nodes": {"added", "unchanged"},
    "plan_get": {"title", "goal", "counts", "nodes"},
    "plan_ready": {"nodes"},
    "node_claim": {"lease_id", "fence", "expires_at", "brief", "context"},
    "node_update": {"status", "expires_at"},
    "node_attach": {"artifact_id"},
    "ctx_get": {"items", "truncated", "verified_commit"},
    "kb_add": {"kb_id", "note_ref"},
    "kb_impact": {"layers", "truncated", "canvas_note"},
    "handover": {"handover_id", "note_ref", "goal", "diff_stat", "nodes"},
    "plan_list": {"plans"},
}
_ERROR_ARGS: dict[str, dict[str, object]] = {
    "plan_create": {"title": "Plan", "goal": "Ship"},
    "plan_add_nodes": {"plan_id": "plan-1", "nodes": []},
    "plan_get": {"plan_id": "plan-1"},
    "plan_ready": {},
    "node_claim": {"node_id": "node-1"},
    "node_update": {"node_id": "node-1", "fence": 0},
    "node_attach": {"node_id": "node-1", "fence": 0, "kind": "note", "ref": "n"},
    "ctx_get": {},
    "kb_add": {"type": "decision", "title": "Keep", "body": "yes", "source": "https://example.com/a"},
    "kb_impact": {"target": "missing"},
    "handover": {"action": "read"},
    "plan_list": {},
}


class _Clock:
    def __init__(self) -> None:
        self.moment = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.moment


def _node(key: str, *, depends_on: list[str] | None = None, paths: list[str] | None = None) -> dict[str, object]:
    return {
        "key": key,
        "title": f"Node {key}",
        "objective": "the node is done",
        "depends_on": [] if depends_on is None else depends_on,
        "paths": [] if paths is None else paths,
    }


def _server(
    clock: _Clock,
    tab_id: str,
    *,
    worktree: Path | None = None,
    board: PlanBoard | None = None,
    project_id: str = "proj",
) -> tuple[PlanBoard, MCPServer]:
    plans = PlanBoard(now=clock) if board is None else board
    context = TabContext(tab_id=tab_id, project_id=project_id, worktree=worktree, agent="claude")
    return plans, build_server(plans, context)


def _body(result: CallToolResult) -> dict[str, object]:
    raw = result.structured_content
    assert isinstance(raw, dict)
    body: dict[str, object] = {}
    for key, value in raw.items():
        assert isinstance(key, str)
        body[key] = value
    return body


def _error(result: CallToolResult) -> dict[str, object]:
    found = _body(result)["error"]
    assert isinstance(found, dict)
    error: dict[str, object] = {}
    for key, value in found.items():
        assert isinstance(key, str)
        error[key] = value
    return error


def _properties(tool: Tool) -> set[str]:
    schema = tool.output_schema
    assert isinstance(schema, dict)
    props = schema.get("properties")
    assert isinstance(props, dict)
    return {key for key in props if isinstance(key, str)}


def _node_id(body: dict[str, object], key: str) -> str:
    nodes = body["nodes"]
    assert isinstance(nodes, list)
    for item in nodes:
        assert isinstance(item, dict)
        if item.get("key") != key:
            continue
        found = item.get("node_id")
        assert isinstance(found, str)
        return found
    message = f"missing {key}"
    raise AssertionError(message)


async def _call(client: Client, name: str, arguments: dict[str, object]) -> dict[str, object]:
    result = await client.call_tool(name, arguments)
    assert result.is_error is not True
    return _body(result)


def test_mcp_tools_schema_content_and_hint(tmp_path: Path) -> None:
    asyncio.run(_each_tool(tmp_path))


def test_mcp_tools_fence() -> None:
    asyncio.run(_fence())


def test_mcp_tools_expiry() -> None:
    asyncio.run(_expiry())


def test_mcp_tools_paths_overlap() -> None:
    asyncio.run(_overlap())


def test_mcp_tools_cycle() -> None:
    asyncio.run(_cycle())


async def _each_tool(worktree: Path) -> None:
    clock = _Clock()
    _plans, server = _server(clock, "tab-a", worktree=worktree)
    _empty, denied = _server(clock, "")
    async with Client(server) as client, Client(denied) as blocked:
        listed = await client.list_tools()
        tools = {tool.name: tool for tool in listed.tools}
        assert set(tools) == set(_FIELDS)
        for name, tool in tools.items():
            _check_tool(name, tool)
            failed = await blocked.call_tool(name, _ERROR_ARGS[name])
            assert failed.is_error is True
            error = _error(failed)
            assert error["reason"] == "INGEST_UNAUTHORIZED"
            assert error["hint"] == _HINT
        await _success(client, worktree)


def _check_tool(name: str, tool: Tool) -> None:
    marks = tool.annotations
    assert marks is not None
    assert marks.destructive_hint is False
    assert marks.open_world_hint is False
    assert marks.read_only_hint is (name in _READ)
    assert marks.idempotent_hint is (name in _READ or name in _IDEMPOTENT)
    assert _FIELDS[name] <= _properties(tool)


async def _success(client: Client, worktree: Path) -> None:
    created = await _call(
        client,
        "plan_create",
        {"title": "Plan", "goal": "Ship", "nodes": [_node("A-1", paths=["src/a.py"])]},
    )
    assert created["canvas_note"] == f"aconsole://plan/{created['plan_id']}"
    plan_id = created["plan_id"]
    assert isinstance(plan_id, str)
    node_id = _node_id(created, "A-1")
    again = await _call(client, "plan_create", {"title": "Plan", "goal": "Ship", "nodes": []})
    assert again["plan_id"] == plan_id
    added = await _call(
        client,
        "plan_add_nodes",
        {"plan_id": plan_id, "nodes": [_node("A-1"), _node("B-1", depends_on=["A-1"])]},
    )
    assert added["unchanged"] == ["A-1"]
    fresh = added["added"]
    assert isinstance(fresh, list)
    assert any(isinstance(item, dict) and item.get("key") == "B-1" for item in fresh)
    got = await _call(client, "plan_get", {"plan_id": plan_id})
    assert got["title"] == "Plan"
    ready = await _call(client, "plan_ready", {"plan_id": plan_id})
    ready_nodes = ready["nodes"]
    assert isinstance(ready_nodes, list)
    assert len(ready_nodes) == 1
    claimed = await _call(client, "node_claim", {"node_id": node_id})
    assert claimed["fence"] == 1
    updated = await _call(client, "node_update", {"node_id": node_id, "fence": 1})
    assert updated["status"] == "in_progress"
    attached = await _call(
        client, "node_attach", {"node_id": node_id, "fence": 1, "kind": "note", "ref": "note-1", "title": "Note"}
    )
    repeat = await _call(client, "node_attach", {"node_id": node_id, "fence": 1, "kind": "note", "ref": "note-1"})
    assert attached["artifact_id"] == repeat["artifact_id"]
    (worktree / "src").mkdir()
    (worktree / "src" / "a.py").write_text("value = 1\n", encoding="utf-8")
    fact = await _call(
        client,
        "kb_add",
        {
            "type": "fact",
            "title": "Value",
            "body": "value is one",
            "source": "src/a.py#L1-1",
            "code_refs": ["src/a.py"],
        },
    )
    missing = await client.call_tool(
        "kb_add",
        {"type": "fact", "title": "Gone", "body": "no file", "source": "src/missing.py#L1-1"},
    )
    assert missing.is_error is True
    assert _error(missing)["reason"] == "PATH_NOT_ALLOWED"
    found = await _call(client, "ctx_get", {"query": "value"})
    items = found["items"]
    assert isinstance(items, list)
    assert len(items) == 1
    kb_id = fact["kb_id"]
    assert isinstance(kb_id, str)
    impact = await _call(client, "kb_impact", {"target": kb_id, "hops": 1})
    layers = impact["layers"]
    assert isinstance(layers, list)
    assert layers == [["src/a.py"]]
    written = await _call(client, "handover", {"action": "write", "goal": "leave the tab"})
    assert isinstance(written["handover_id"], str)
    read = await _call(client, "handover", {"action": "read"})
    assert read["goal"] == "leave the tab"
    listed = await _call(client, "plan_list", {})
    plans = listed["plans"]
    assert isinstance(plans, list)
    assert len(plans) == 1


async def _fence() -> None:
    clock = _Clock()
    _plans, server = _server(clock, "tab-a")
    async with Client(server) as client:
        created = await _call(client, "plan_create", {"title": "Plan", "goal": "Ship", "nodes": [_node("A-1")]})
        node_id = _node_id(created, "A-1")
        _ = await _call(client, "node_claim", {"node_id": node_id})
        failed = await client.call_tool("node_update", {"node_id": node_id, "fence": 0, "note": "stale"})
        assert failed.is_error is True
        error = _error(failed)
        assert error["reason"] == "LEASE_STALE_FENCE"
        assert error["hint"] == "call plan_get for the current fence, then retry node_update"


async def _expiry() -> None:
    clock = _Clock()
    _plans, server = _server(clock, "tab-a")
    async with Client(server) as client:
        created = await _call(client, "plan_create", {"title": "Plan", "goal": "Ship", "nodes": [_node("A-1")]})
        plan_id = created["plan_id"]
        node_id = _node_id(created, "A-1")
        claimed = await _call(client, "node_claim", {"node_id": node_id})
        fence = claimed["fence"]
        clock.moment += timedelta(minutes=16)
        failed = await client.call_tool("node_update", {"node_id": node_id, "fence": fence})
        assert failed.is_error is True
        error = _error(failed)
        assert error["reason"] == "LEASE_EXPIRED"
        assert error["hint"] == "call node_claim again"
        got = await _call(client, "plan_get", {"plan_id": plan_id})
        nodes = got["nodes"]
        assert isinstance(nodes, list)
        assert isinstance(nodes[0], dict)
        assert nodes[0]["status"] == "pending"


async def _overlap() -> None:
    clock = _Clock()
    board = PlanBoard(now=clock)
    _left_board, left = _server(clock, "tab-a", board=board)
    _right_board, right = _server(clock, "tab-b", board=board)
    async with Client(left) as first:
        created = await _call(
            first,
            "plan_create",
            {
                "title": "Plan",
                "goal": "Ship",
                "nodes": [_node("A-1", paths=["src/**"]), _node("B-1", paths=["src/a.py"])],
            },
        )
        _ = await _call(first, "node_claim", {"node_id": _node_id(created, "A-1")})
        other = _node_id(created, "B-1")
    async with Client(right) as second:
        failed = await second.call_tool("node_claim", {"node_id": other})
        assert failed.is_error is True
        error = _error(failed)
        assert error["reason"] == "PATHS_OVERLAP"
        assert error["hint"] == "call plan_ready for a node whose paths do not overlap an in-progress node"


async def _cycle() -> None:
    clock = _Clock()
    _plans, server = _server(clock, "tab-a")
    async with Client(server) as client:
        failed = await client.call_tool(
            "plan_create",
            {
                "title": "Cycle",
                "goal": "Ship",
                "nodes": [_node("A-1", depends_on=["B-1"]), _node("B-1", depends_on=["A-1"])],
            },
        )
        assert failed.is_error is True
        error = _error(failed)
        assert error["reason"] == "PLAN_CYCLE"
        assert error["hint"] == "call plan_get and drop the depends_on edge that closes the cycle"
        listed = await _call(client, "plan_list", {"status": "all"})
        assert listed["plans"] == []
