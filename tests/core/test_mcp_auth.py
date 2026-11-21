"""Only a tab's token can reach a tool. Anything else can at most list them."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import msgspec
import pytest

from aconsole.core.bootstrap import Daemon
from aconsole.core.infrastructure.mcp.server import TabTokens, beside_ingest
from aconsole.core.infrastructure.mcp.tools import TabContext
from tests.socket_paths import short_socket_path

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    type _Message = Mapping[str, object]

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

    async def mcp(scope: _Message, receive: object, send: object) -> None:
        _ = scope, receive, send
        reached.append("mcp")

    async def ingest(scope: _Message, receive: object, send: object) -> None:
        _ = scope, receive, send
        reached.append("ingest")

    async def send(message: _Message) -> None:
        sent.append(message)

    async def receive() -> _Message:
        return {"type": "http.request"}

    app = beside_ingest(ingest, mcp, tokens)
    headers = [] if header is None else [(b"authorization", header)]
    scope: _Message = {"type": "http", "path": "/mcp", "headers": headers}
    asyncio.run(app(scope, receive, send))
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
_REQUIRED_ARGUMENTS: dict[str, dict[str, object]] = {
    "index_neighbors": {"targets": ["main.py"]},
    "index_read": {"refs": [{"path": "main.py"}]},
}


class _Argument(msgspec.Struct):
    type: str = "string"
    enum: tuple[object, ...] = ()


class _ToolSchema(msgspec.Struct):
    properties: dict[str, _Argument] = msgspec.field(default_factory=dict)
    required: tuple[str, ...] = ()


def _sample(schema: _Argument) -> object:
    """A value the schema accepts, so that only the tab check can refuse the call."""
    return schema.enum[0] if schema.enum else _SAMPLES.get(schema.type, "x")


def test_the_daemon_refuses_every_tool_without_a_tab(tmp_path: Path) -> None:
    async def scenario() -> None:
        daemon = Daemon(short_socket_path(tmp_path / "core.sock"), idle_s=30, database=None)
        mcp = daemon._mcp  # pyright: ignore[reportPrivateUsage] -- test the actual server wired by the daemon
        tools = await mcp.list_tools()
        assert tools
        created = await mcp.call_tool("plan_create", {"title": "t", "goal": "g"})
        assert "INGEST_UNAUTHORIZED" in str(created)
        assert daemon._plans.views() == ()  # pyright: ignore[reportPrivateUsage] -- verify absence of side effects
        for tool in tools:
            schema = msgspec.convert(tool.input_schema, type=_ToolSchema)
            arguments = {name: _sample(schema.properties[name]) for name in schema.required}
            arguments.update(_REQUIRED_ARGUMENTS.get(tool.name, {}))
            answer = await mcp.call_tool(tool.name, arguments)
            assert "INGEST_UNAUTHORIZED" in str(answer), tool.name

    asyncio.run(scenario())
