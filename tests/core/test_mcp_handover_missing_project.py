"""MCP must not acknowledge a handover SQLite refused to retain."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from mcp import Client

from aconsole.core.infrastructure.mcp.server import build_server
from aconsole.core.infrastructure.mcp.tools import PlanBoard, TabContext
from aconsole.core.infrastructure.persistence.repositories.knowledge import KnowledgeDatabase
from aconsole.core.infrastructure.persistence.repositories.tabs import TabDatabase
from aconsole.core.infrastructure.persistence.startup import prepare_database
from tests.core.test_mcp_knowledge_recovery import _database
from tests.core.test_mcp_tools import _call, _error
from tests.core.test_store_recovery import _WHEN, _tab

if TYPE_CHECKING:
    from pathlib import Path


def test_missing_project_handover_is_rejected_and_can_be_retried_after_project_restore(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    prepare_database(path, now=_WHEN)
    context = TabContext(tab_id="tab-one", project_id="project", worktree=None, agent="claude")
    with _database(path) as (board, writer):
        async def scenario() -> str:
            async with Client(build_server(board, context)) as client:
                result = await client.call_tool("handover", {"action": "write", "goal": "Keep this"})
                assert result.is_error is True
                assert _error(result)["reason"] == "INTERNAL_ERROR"
                assert (await _call(client, "handover", {"action": "read"})).get("handover_id") is None
                assert KnowledgeDatabase(writer).load() == ((), ())
                TabDatabase(writer).save(_tab())
                written = await _call(client, "handover", {"action": "write", "goal": "Retry"})
                identity = written["handover_id"]
                assert isinstance(identity, str)
                assert (await _call(client, "handover", {"action": "read"}))["goal"] == "Retry"
                return identity

        handover_id = asyncio.run(scenario())
    with _database(path) as (_, writer):
        restarted = PlanBoard()
        restarted.use_knowledge(KnowledgeDatabase(writer))

        async def verify() -> None:
            async with Client(build_server(restarted, context)) as client:
                found = await _call(client, "handover", {"action": "read"})
                assert found["handover_id"] == handover_id
                assert found["goal"] == "Retry"

        asyncio.run(verify())
