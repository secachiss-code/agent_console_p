"""Exercise file, symbol, graph and lint reads through the authenticated MCP surface."""

import asyncio
import sys
from pathlib import Path

import pytest
from mcp import Client

from aconsole.core.infrastructure.codegraph.index import CodeGraphIndex
from aconsole.core.infrastructure.codegraph.runner import ExtractorRunner
from aconsole.core.infrastructure.git.runner import GitRunner
from tests.core.test_mcp_tools import _call, _Clock, _error, _node, _node_id, _server


def test_project_index_search_read_and_worktree_isolation(tmp_path: Path) -> None:
    left, right = tmp_path / "left", tmp_path / "right"
    left.mkdir()
    right.mkdir()
    for root, number in ((left, 1), (right, 2)):
        _ = (root / "pyproject.toml").write_text("[tool.ruff]\n")
        _ = (root / "api.py").write_text(f"def build_graph():\n    return {number}\n")
        _ = (root / "main.py").write_text("from api import build_graph\n")
        _ = (root / "test_api.py").write_text("def test_example(): ...\n")
        _ = (root / "README.md").write_text("Graph documentation with a unique phrase.\n")
    (left / "escape.md").symlink_to(right / "README.md")
    board, server = _server(_Clock(), "left", worktree=left)
    _, peer = _server(_Clock(), "right", worktree=right, board=board)
    board.code_graph = CodeGraphIndex(ExtractorRunner(), lambda _project: left)

    async def scenario() -> None:
        async with Client(server) as client, Client(peer) as other:
            query = {"query": "build_graph", "intent": "change"}
            found = await _call(client, "index_find", query)
            match = found["items"][0]
            assert match["kind"] == "symbol"
            assert match["related"][0]["path"] == "test_api.py"
            args = {"refs": [match["ref"]], "expected_revision": found["revision"]}
            source = await _call(client, "index_read", args)
            assert "return 1" in source["items"][0]["text"]
            wrong = await other.call_tool("index_read", args)
            assert _error(wrong)["reason"] == "INDEX_REVISION_STALE"

            docs = await _call(client, "index_find", {"query": "unique phrase"})
            assert [item["ref"]["path"] for item in docs["items"]] == ["README.md"]
            adjacent = await _call(client, "index_neighbors", {"targets": ["api.py"], "direction": "in"})
            assert adjacent["items"][0]["ref"]["path"] == "main.py"
            escaped = await _call(client, "index_read", {"refs": [{"path": "escape.md"}, {"path": "../right/api.py"}]})
            assert [item["status"] for item in escaped["items"]] == ["unavailable", "unavailable"]

            plan = await _call(client, "plan_create", {
                "title": "Context", "goal": "Navigate", "nodes": [_node("G-1", paths=["api.py"])],
            })
            task = {"node_id": _node_id(plan, "G-1")}
            context = await _call(client, "index_task_context", task)
            assert context["items"][0]["ref"]["path"] == "api.py"
            unchanged = await _call(client, "index_task_context", {**task, "since_digest": context["digest"]})
            assert unchanged["unchanged"] is True
            _ = (left / "api.py").write_text("def build_graph():\n    return 3\n")
            stale = await client.call_tool("index_read", args)
            assert _error(stale)["reason"] == "INDEX_REVISION_STALE"

    asyncio.run(scenario())


def test_project_index_ruff_is_read_only_and_cache_follows_config(tmp_path: Path) -> None:
    if not (Path(sys.prefix) / "bin" / "ruff").is_file():
        pytest.skip("Ruff is missing from this environment")
    git = GitRunner()
    _ = git.write(tmp_path, "init", "-q")
    source = tmp_path / "app.py"
    config = tmp_path / "ruff.toml"
    _ = source.write_text("value = 1\n")
    _ = config.write_text("[lint]\nselect = ['F401']\n")
    _ = git.write(tmp_path, "add", "--", "app.py", "ruff.toml")
    _ = git.write(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-qm", "base")
    before = "import pathlib\n"
    _ = source.write_text(before)
    _board, server = _server(_Clock(), "lint", worktree=tmp_path)

    async def scenario() -> None:
        async with Client(server) as client:
            lint = await _call(client, "index_lint", {"scope": "changed"})
            assert lint["state"] == "findings"
            assert lint["diagnostics"][0]["code"] == "F401"
            assert lint["diagnostics"][0]["path"] == "app.py"
            assert (await _call(client, "index_lint", {"scope": "changed"}))["cached"] is True
            assert source.read_text() == before
            _ = config.write_text("[lint]\nselect = ['E9']\n")
            changed = await _call(client, "index_lint", {"scope": "changed"})
            assert changed["state"] == "clean"
            assert not changed.get("cached")

    asyncio.run(scenario())
