"""Only a tab's token can reach a tool. Anything else can at most list them."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from aconsole.core.bootstrap import Daemon
from aconsole.core.infrastructure.mcp.server import TabTokens, beside_ingest
from aconsole.core.infrastructure.mcp.tools import TabContext
from tests.socket_paths import short_socket_path

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, MutableMapping
    from pathlib import Path

    type _Message = MutableMapping[str, object]

_CONTEXT = TabContext(tab_id="t1", project_id="p1", worktree=None, agent="claude")
_UNAUTHORIZED = 401


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, "absent"),
        ("Bearer good", _CONTEXT),
        ("bearer good", _CONTEXT),
        ("Bearer unknown", None),
        ("good", None),
        ("Basic good", None),
        ("Bearer", None),
        ("", None),
    ],
)
def test_a_header_is_absent_valid_or_refused(header: str | None, expected: object) -> None:
    tokens = TabTokens()
    tokens.register("t1", "good", _CONTEXT)
    assert tokens.resolve(header) == expected


def _call(header: bytes | None) -> tuple[list[_Message], list[str]]:
    sent: list[_Message] = []
    reached: list[str] = []
    tokens = TabTokens()
    tokens.register("t1", "good", _CONTEXT)

    async def mcp(_scope: _Message, _receive: object, _send: object) -> None:
        reached.append("mcp")

    async def ingest(_scope: _Message, _receive: object, _send: object) -> None:
        reached.append("ingest")

    async def send(message: _Message) -> None:
        sent.append(message)

    async def receive() -> _Message:
        return {"type": "http.request"}

    app: Callable[[_Message, object, object], Awaitable[None]] = beside_ingest(ingest, mcp, tokens)  # pyright: ignore[reportAssignmentType] -- ASGI apps
    headers = [] if header is None else [(b"authorization", header)]
    scope: _Message = {"type": "http", "path": "/mcp", "headers": headers}
    asyncio.run(app(scope, receive, send))  # pyright: ignore[reportArgumentType] -- `app` returns a coroutine
    return sent, reached


@pytest.mark.parametrize("header", [b"good", b"Basic good", b"Bearer unknown", b"Bearer"])
def test_a_bad_header_never_reaches_the_tools(header: bytes) -> None:
    sent, reached = _call(header)
    assert reached == []
    assert sent[0]["status"] == _UNAUTHORIZED


def test_a_good_token_and_no_token_reach_the_server() -> None:
    assert _call(b"Bearer good")[1] == ["mcp"]
    assert _call(None)[1] == ["mcp"]


_SAMPLES: dict[str, object] = {"string": "x", "array": [], "integer": 1, "number": 1, "boolean": True, "object": {}}


def _sample(schema: dict[str, object]) -> object:
    """A value the schema accepts, so that only the tab check can refuse the call."""
    choices = schema.get("enum")
    if isinstance(choices, list) and choices:
        return choices[0]
    return _SAMPLES.get(str(schema.get("type")), "x")


def test_the_daemon_refuses_every_tool_without_a_tab(tmp_path: Path) -> None:
    async def scenario() -> None:
        daemon = Daemon(short_socket_path(tmp_path / "core.sock"), idle_s=30, database=None)
        tools = await daemon._mcp.list_tools()
        assert tools
        created = await daemon._mcp.call_tool("plan_create", {"title": "t", "goal": "g"})
        assert "INGEST_UNAUTHORIZED" in str(created)
        assert daemon._plans.views() == ()
        for tool in tools:
            properties = tool.input_schema.get("properties", {})
            required = tool.input_schema.get("required", [])
            arguments = {name: _sample(properties[name]) for name in required}
            answer = await daemon._mcp.call_tool(tool.name, arguments)
            assert "INGEST_UNAUTHORIZED" in str(answer), tool.name

    asyncio.run(scenario())
