"""Each plan tool is called through ``Client(server)``: schema, content, and a hinted error."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from mcp import Client
from mcp.types import TextContent

from aconsole.core.infrastructure.mcp.server import build_server
from aconsole.core.infrastructure.mcp.tools import PlanBoard, TabContext
from tests.gate_profiles import MICRO_PROFILE
from tests.mcp_names import ALL_TOOLS

if TYPE_CHECKING:
    from pathlib import Path

    from mcp.server.mcpserver import MCPServer
    from mcp.types import CallToolResult, Tool

_HINT = "open a tab and pass Authorization: Bearer <tab token>"
_READ = frozenset({
    "plan_get", "plan_graph", "plan_node_get", "plan_ready", "ctx_get", "kb_impact", "plan_list",
    "index_overview", "index_find", "index_neighbors", "index_read", "index_task_context", "index_lint",
})
_IDEMPOTENT = frozenset({
    "node_escalate", "plan_create", "plan_add_nodes", "plan_save", "node_attach",
    "step_begin", "step_submit", "step_reset",
})
_ERROR_ARGS: dict[str, dict[str, object]] = {
    "step_begin": {"node_id": "node-1", "fence": 0},
    "step_submit": {"node_id": "node-1", "fence": 0, "step": "spec"},
    "step_reset": {"node_id": "node-1", "fence": 0},
    "node_escalate": {"node_id": "node-1", "fence": 1, "reason": "Review"},
    "gate_run": {"node_id": "node-1", "fence": 0, "tier": "micro"},
    "plan_create": {"title": "Plan", "goal": "Ship"},
    "plan_add_nodes": {"plan_id": "plan-1", "nodes": []},
    "plan_save": {"title": "Plan", "goal": "Ship"},
    "plan_delete": {"plan_id": "plan-1"},
    "plan_get": {"plan_id": "plan-1"},
    "plan_graph": {"plan_id": "plan-1"},
    "plan_node_get": {"node_id": "node-1"},
    "index_overview": {},
    "index_find": {"query": "a"},
    "index_neighbors": {"targets": ["a.py"]},
    "index_read": {"refs": [{"path": "a.py"}]},
    "index_task_context": {"node_id": "node-1"},
    "index_lint": {"scope": "targets", "targets": ["a.py"]},
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
    raw: object = result.structured_content if result.is_error else json.loads(_text(result))
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


def _lean(tool: Tool) -> bool:
    """No output schema, and no generated title or ``null`` alternative in the argument schema."""
    schema = json.dumps(tool.input_schema)
    return tool.output_schema is None and '"title": "' not in schema and '{"type": "null"}' not in schema


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


def test_plan_graph_and_details_scope_and_pagination() -> None:
    async def scenario() -> None:
        board, server = _server(_Clock(), "planner")
        _, peer = _server(_Clock(), "peer", board=board)
        _, foreign = _server(_Clock(), "elsewhere", board=board, project_id="foreign")
        async with Client(server) as client, Client(peer) as second, Client(foreign) as other:
            created = await _call(client, "plan_create", {
                "title": "Graph", "goal": "Navigate",
                "nodes": [
                    {**_node("D-0"), "level": 0, "invariants": "Small graph", "gate_profile": MICRO_PROFILE},
                    {**_node("N-1", paths=["src/a.py"]), "parent": "D-0", "acceptance": ["green"]},
                    {**_node("N-2", depends_on=["N-1"]), "related": ["N-1"]},
                ],
            })
            plan_id = str(created["plan_id"])
            first, second_id, root = (_node_id(created, key) for key in ("N-1", "N-2", "D-0"))
            summary = await _call(client, "plan_graph", {"plan_id": plan_id})
            assert [(row["node_id"], row.get("children", 0)) for row in summary["nodes"]] == [(root, 1), (second_id, 0)]
            query = {"plan_id": plan_id, "mode": "local", "anchor": first, "limit": 1}
            page = await _call(client, "plan_graph", query)
            assert page["nodes"][0]["node_id"] == first
            cursor = page["cursor"]
            replay = await second.call_tool("plan_graph", {**query, "cursor": cursor})
            assert _error(replay)["reason"] == "GRAPH_CURSOR_STALE"
            wrong_project = await other.call_tool("plan_node_get", {"node_id": first})
            assert _error(wrong_project)["reason"] == "NODE_NOT_FOUND"
            edges: list[dict[str, str]] = []
            while page.get("cursor") is not None:
                page = await _call(client, "plan_graph", {**query, "cursor": page["cursor"]})
                edges.extend(page["edges"])
            assert {edge["kind"] for edge in edges} == {"decomposes", "depends_on", "related"}
            assert {"source": first, "target": second_id, "kind": "depends_on"} in edges

            detail_query = {"node_id": first, "sections": ["contract"], "limit": 1}
            detail = await _call(client, "plan_node_get", detail_query)
            seen = {detail["entries"][0]["field"]: detail["entries"][0]["value"]}
            while detail.get("cursor") is not None:
                detail = await _call(client, "plan_node_get", {**detail_query, "cursor": detail["cursor"]})
                entry = detail["entries"][0]
                seen[entry["field"]] = entry["value"]
            assert seen["path"] == "src/a.py"
            assert seen["acceptance"] == "green"

            _ = await _call(client, "node_claim", {"node_id": first})
            expired = await client.call_tool("plan_graph", {**query, "cursor": cursor})
            assert _error(expired)["reason"] == "GRAPH_CURSOR_STALE"

    asyncio.run(scenario())


def test_execute_cannot_list_or_call_graph_mutations() -> None:
    async def scenario() -> None:
        board, _server_unused = _server(_Clock(), "planner")
        ctx = TabContext(tab_id="executor", project_id="proj", worktree=None, agent="codex", mode="execute")
        server = build_server(board, ctx)
        async with Client(server) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert not names.intersection(
                {"plan_create", "plan_add_nodes", "plan_save", "plan_delete", "change_propose"}
            )
            for name, arguments in (
                ("plan_create", {"title": "Plan", "goal": "Goal"}),
                ("plan_add_nodes", {"plan_id": "missing", "nodes": []}),
                ("plan_save", {"title": "Plan", "goal": "Goal"}),
                ("plan_delete", {"plan_id": "missing"}),
            ):
                result = await client.call_tool(name, arguments)
                assert result.is_error
                assert _error(result)["reason"] == "PLAN_ROLE"
            assert board.views() == ()

    asyncio.run(scenario())


def test_planning_requires_full_contract_and_defaults_pipeline_on() -> None:
    async def scenario() -> None:
        board = PlanBoard()
        ctx = TabContext(tab_id="planner", project_id="proj", worktree=None, agent="codex", mode="plan")
        async with Client(build_server(board, ctx)) as client:
            denied = await client.call_tool("plan_create", {"title": "Plan", "goal": "Goal", "nodes": [_node("C-1")]})
            assert denied.is_error
            assert _error(denied)["reason"] == "NODE_CONTRACT"
            assert board.views() == ()
            created = await _call(client, "plan_create", {
                "title": "Plan", "goal": "Goal", "nodes": [
                    {**_node("D-0", paths=["src/**"]), "level": 0, "invariants": "pure domain",
                     "gate_profile": MICRO_PROFILE, "acceptance": ["green"]},
                    {**_node("C-1", paths=["src/a.py"]), "parent": "D-0", "test_cmd": "pytest",
                     "acceptance": ["green"]},
                ],
            })
            plan_id = str(created["plan_id"])
            assert board._plans[plan_id].dag.get("C-1").pipeline
            executor = build_server(board, replace(ctx, mode="execute"))
            async with Client(executor) as execution:
                denied = await execution.call_tool("plan_add_nodes", {"plan_id": plan_id, "nodes": [_node("C-2")]})
                assert _error(denied)["reason"] == "PLAN_ROLE"
            assert len(board.view(plan_id).nodes) == 2
            saved = await _call(client, "plan_save", {
                "plan_id": plan_id,
                "title": "Plan renamed",
                "goal": "Goal",
                "nodes": [
                    {**_node("D-0", paths=["src/**"]), "level": 0, "invariants": "pure domain",
                     "gate_profile": MICRO_PROFILE, "acceptance": ["green"]},
                    {**_node("C-2", paths=["src/b.py"]), "parent": "D-0", "test_cmd": "pytest",
                     "acceptance": ["green"]},
                ],
            })
            assert saved["plan_id"] == plan_id
            assert saved["title"] == "Plan renamed"
            assert {node["key"] for node in saved["nodes"]} == {"D-0", "C-2"}
            removed = await _call(client, "plan_delete", {"plan_id": plan_id})
            assert removed == {"plan_id": plan_id}
            assert board.view(plan_id) is None

    asyncio.run(scenario())


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
        assert set(tools) == ALL_TOOLS
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
    assert marks.destructive_hint is (name == "plan_delete")
    assert marks.open_world_hint is False
    assert marks.read_only_hint is (name in _READ)
    assert marks.idempotent_hint is (name in _READ or name in _IDEMPOTENT)
    assert _lean(tool)


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


def test_mcp_text_is_compact() -> None:
    asyncio.run(_compact())


async def _compact() -> None:
    clock = _Clock()
    _plans, server = _server(clock, "tab-a")
    async with Client(server) as client:
        created = await _call(client, "plan_create", {"title": "Plan", "goal": "Ship", "nodes": [_node("A-1")]})
        result = await client.call_tool("plan_get", {"plan_id": created["plan_id"]})
        text = _text(result)
        assert "\n" not in text
        assert ": " not in text
        assert "null" not in text
        assert "[]" not in text
        failed = await client.call_tool("node_update", {"node_id": "node-404", "fence": 0})
        assert failed.is_error is True
        assert "Next: " in _text(failed)


def _text(result: CallToolResult) -> str:
    return "".join(block.text for block in result.content if isinstance(block, TextContent))


def test_mcp_plan_get_since() -> None:
    asyncio.run(_since())


async def _since() -> None:
    clock = _Clock()
    _plans, server = _server(clock, "tab-a")
    async with Client(server) as client:
        nodes = [_node("A-1"), _node("B-1", depends_on=["A-1"]), _node("C-1")]
        created = await _call(client, "plan_create", {"title": "Plan", "goal": "Ship", "nodes": nodes})
        plan_id = created["plan_id"]
        first = await _call(client, "plan_get", {"plan_id": plan_id})
        revision = first["revision"]
        same = await _call(client, "plan_get", {"plan_id": plan_id, "since": revision})
        assert same["changed_only"] is True
        assert same["nodes"] == []
        assert same["revision"] == revision
        _ = await _call(client, "node_claim", {"node_id": _node_id(created, "A-1")})
        delta = await _call(client, "plan_get", {"plan_id": plan_id, "since": revision})
        assert delta["changed_only"] is True
        assert delta["revision"] != revision
        assert [item["key"] for item in _nodes(delta)] == ["A-1"]
        unknown = await _call(client, "plan_get", {"plan_id": plan_id, "since": "gone.1"})
        assert "changed_only" not in unknown
        assert len(_nodes(unknown)) == len(nodes)
        detailed = await _call(
            client, "plan_get", {"plan_id": plan_id, "since": revision, "response_format": "detailed"}
        )
        assert len(_nodes(detailed)) == len(nodes)


def _nodes(body: dict[str, object]) -> list[dict[str, object]]:
    found = body["nodes"]
    assert isinstance(found, list)
    return [item for item in found if isinstance(item, dict)]


def test_mcp_counts_what_each_answer_sends() -> None:
    asyncio.run(_counted())


async def _counted() -> None:
    clock = _Clock()
    heard: list[tuple[str, int]] = []
    board = PlanBoard(now=clock)
    context = TabContext(tab_id="tab-a", project_id="proj", worktree=None, agent="claude")
    server = build_server(board, context, lambda tab, size: heard.append((tab, size)))
    async with Client(server) as client:
        created = await client.call_tool("plan_create", {"title": "Plan", "goal": "Ship", "nodes": [_node("A-1")]})
        failed = await client.call_tool("node_update", {"node_id": "node-404", "fence": 0})
    assert heard == [
        ("tab-a", len(_text(created).encode())),
        ("tab-a", len(_text(failed).encode())),
    ]


def test_parent_cycle_is_not_saved() -> None:
    import pytest

    from aconsole.core.infrastructure.mcp.tools import NodeIn, _ToolError

    board = PlanBoard()
    ctx = TabContext(tab_id="t", project_id="p", worktree=None, agent="codex")
    created = board.create(ctx, "Plan", "goal", [NodeIn(key="R-0", title="Root", objective="do")])
    with pytest.raises(_ToolError):
        board.add_nodes(
            ctx,
            created.plan_id,
            [
                NodeIn(key="A-1", title="Alpha", objective="do", parent="B-1"),
                NodeIn(key="B-1", title="Beta", objective="do", parent="A-1"),
            ],
        )
    assert tuple(board._plans[created.plan_id].meta) == ("R-0",)


def test_mcp_hdag_scope_rejection_is_atomic() -> None:
    asyncio.run(_hdag_scope())


async def _hdag_scope() -> None:
    board, server = _server(_Clock(), "tab-a")
    async with Client(server) as client:
        created = await _call(
            client,
            "plan_create",
            {
                "title": "H-DAG",
                "goal": "Ship",
                "nodes": [
                    {
                        **_node("CORE-0", paths=["src/**"]),
                        "level": 0,
                        "invariants": "Pure domain",
                        "gate_profile": MICRO_PROFILE,
                    },
                ],
            },
        )
        plan_id = created["plan_id"]
        ready = await _call(client, "plan_ready", {"plan_id": plan_id})
        assert ready["nodes"] == []
        rejected = await client.call_tool(
            "plan_add_nodes",
            {
                "plan_id": plan_id,
                "nodes": [
                    {**_node("BAD-1", paths=["tests/outside.py"]), "parent": "CORE-0", "pipeline": True},
                ],
            },
        )
        error = _error(rejected)
        assert error["reason"] == "NODE_CONTRACT"
        assert error["code"] == 4013
        assert len(next(iter(board._plans.values())).meta) == 1
        await _call(
            client,
            "plan_add_nodes",
            {
                "plan_id": plan_id,
                "nodes": [
                    {**_node("CORE-1", paths=["src/core.py"]), "parent": "CORE-0", "pipeline": True,
                     "test_cmd": "pytest", "acceptance": ["green"]},
                ],
            },
        )
        ready = await _call(client, "plan_ready", {"plan_id": plan_id})
        assert ready["nodes"] == []
        board.freeze(str(plan_id), {"CORE-1": ()}, graph_present=True)
        ready = await _call(client, "plan_ready", {"plan_id": plan_id})
        assert [node["key"] for node in _nodes(ready)] == ["CORE-1"]


def test_pipeline_draft_cannot_be_claimed_and_freeze_makes_it_ready() -> None:
    async def scenario() -> None:
        board = PlanBoard()
        planner = TabContext(tab_id="planner", project_id="proj", worktree=None, agent="codex", mode="plan")
        async with Client(build_server(board, planner)) as client:
            made = await _call(client, "plan_create", {
                "title": "Freeze contract", "goal": "Ship", "nodes": [
                    {**_node("D-0", paths=["src/**"]), "level": 0, "invariants": "pure",
                     "gate_profile": MICRO_PROFILE, "acceptance": ["green"]},
                    {**_node("C-1", paths=["src/a.py"]), "parent": "D-0", "test_cmd": "pytest",
                     "acceptance": ["green"]},
                ],
            })
        plan_id = str(made["plan_id"])
        node_id = _node_id(made, "C-1")
        async with Client(build_server(board, replace(planner, tab_id="executor", mode="execute"))) as client:
            args = {"plan_id": plan_id, "response_format": "detailed"}
            draft = await _call(client, "plan_get", args)
            assert draft["phase"] == "draft"
            module = _nodes(draft)[1]
            assert module["pipeline"] is True
            assert not module.get("contract_sha256")
            assert module["ready"] is False
            assert (await _call(client, "plan_ready", {"plan_id": plan_id}))["nodes"] == []
            denied = _error(await client.call_tool("node_claim", {"node_id": node_id}))
            assert denied["reason"] == "NODE_CONTRACT"
            assert "Freeze" in str(denied["hint"])
            stored = board.export_plan(plan_id).nodes[1]
            assert stored.fence == 0
            assert stored.lease_id is None
            assert board.view(plan_id).nodes[1].ready is False
            board.freeze(plan_id, {"C-1": ()}, graph_present=True)
            frozen = await _call(client, "plan_get", {**args, "since": draft["revision"]})
            assert frozen["phase"] == "frozen"
            module = next(node for node in _nodes(frozen) if node["key"] == "C-1")
            assert module["ready"] is True
            assert module["contract_sha256"]
            assert board.view(plan_id).nodes[1].ready is True
            assert (await _call(client, "plan_ready", {"plan_id": plan_id}))["nodes"]
            assert (await _call(client, "node_claim", {"node_id": node_id}))["fence"] == 1

    asyncio.run(scenario())
