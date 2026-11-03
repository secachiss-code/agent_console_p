"""Measure what AConsole's MCP tools put in an agent's context.

Builds the tool server in process, fills a plan of 30 nodes, eight decisions and a handover, and prints the bytes of
the tool list and of each answer: the text the agent reads, and the structured copy beside it when there is one. With
ANTHROPIC_API_KEY set it also asks the token counting endpoint how many tokens each text is.

    PYTHONPATH=src .venv-core/bin/python tools/mcp_bench.py
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from mcp import Client

from aconsole.core.infrastructure.mcp.server import build_server
from aconsole.core.infrastructure.mcp.tools import PlanBoard, TabContext

if TYPE_CHECKING:
    from mcp.types import CallToolResult

_NODES: Final = 30
_DECISIONS: Final = 8
_COUNT_URL: Final = "https://api.anthropic.com/v1/messages/count_tokens"
_MODEL: Final = "claude-sonnet-5-5"


def _node(index: int) -> dict[str, object]:
    key = f"N-{index}"
    return {
        "key": key,
        "title": f"Step {index}: wire the limits poller for agent {index % 4}",
        "objective": "The poller asks the agent's backend and folds the answer into the account card.",
        "acceptance": ["tests pass", "the card shows the window"],
        "paths": [f"src/aconsole/core/step_{index}.py"],
        "depends_on": [f"N-{index - 1}"] if index > 1 else [],
    }


def _text(result: CallToolResult) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


def _structured(result: CallToolResult) -> int:
    raw = result.structured_content
    return 0 if raw is None else len(json.dumps(raw, separators=(",", ":")))


def _tokens(text: str) -> int | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    body = json.dumps({"model": _MODEL, "messages": [{"role": "user", "content": text}]}).encode()
    request = urllib.request.Request(
        _COUNT_URL,
        data=body,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as answer:  # noqa: S310 -- a fixed https URL
        document: dict[str, int] = json.loads(answer.read())  # pyright: ignore[reportAny] -- the endpoint's shape
    return document["input_tokens"]


def _row(name: str, text: str, structured: int) -> None:
    tokens = _tokens(text)
    shown = "" if tokens is None else f"{tokens:>8}"
    print(f"{name:<28}{len(text.encode()):>10}{structured:>12}{shown}")  # noqa: T201 -- a console tool


async def _run() -> None:
    board = PlanBoard(now=lambda: datetime(2026, 11, 3, 12, 0, tzinfo=UTC))
    server = build_server(board, TabContext(tab_id="tab-a", project_id="proj", worktree=None, agent="claude"))
    async with Client(server) as client:
        tokens = "  tokens" if os.environ.get("ANTHROPIC_API_KEY") else ""
        print(f"{'answer':<28}{'text, B':>10}{'struct, B':>12}{tokens}")  # noqa: T201 -- a console tool
        listed = await client.list_tools()
        tools = json.dumps([tool.model_dump(mode="json", exclude_none=True, by_alias=True) for tool in listed.tools])
        _row("tools/list", tools, 0)
        nodes = [_node(i) for i in range(1, _NODES + 1)]
        created = await client.call_tool(
            "plan_create", {"title": "Limits", "goal": "Every agent shows its limits", "nodes": nodes}
        )
        _row("plan_create (30 nodes)", _text(created), _structured(created))
        plan_id = json.loads(_text(created))["plan_id"]
        revision = None
        for fmt in ("detailed", "concise"):
            got = await client.call_tool("plan_get", {"plan_id": plan_id, "response_format": fmt})
            _row(f"plan_get {fmt}", _text(got), _structured(got))
            revision = json.loads(_text(got)).get("revision")
        ready = await client.call_tool("plan_ready", {"plan_id": plan_id})
        first = json.loads(_text(ready))["nodes"][0]["node_id"]
        _ = await client.call_tool("node_claim", {"node_id": first})
        if revision is not None:
            delta = await client.call_tool("plan_get", {"plan_id": plan_id, "since": revision})
            _row("plan_get since (1 change)", _text(delta), _structured(delta))
        for index in range(_DECISIONS):
            _ = await client.call_tool(
                "kb_add",
                {
                    "type": "decision",
                    "title": f"Limits decision {index}",
                    "body": "Cursor's monthly allowance is a share, not dollars; the pool is money.",
                    "source": f"https://example.com/decision/{index}",
                },
            )
        found = await client.call_tool("ctx_get", {"query": "limits", "max_items": _DECISIONS})
        _row(f"ctx_get ({_DECISIONS} items)", _text(found), _structured(found))
        _ = await client.call_tool("handover", {"action": "write", "goal": "finish the limits", "done": ["cursor"]})
        handed = await client.call_tool("handover", {"action": "read"})
        _row("handover read", _text(handed), _structured(handed))
        plans = await client.call_tool("plan_list", {})
        _row("plan_list", _text(plans), _structured(plans))


if __name__ == "__main__":
    asyncio.run(_run())
