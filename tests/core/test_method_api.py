"""The local method API calls the socket dispatcher and refuses a tab bearer."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from aconsole.contracts.app import HealthResult
from aconsole.contracts.generate import documents
from aconsole.contracts.methods import rest_method, rest_methods, rest_timeout_ms
from aconsole.core.infrastructure.http.api import MethodApi, issue_api_token
from aconsole.core.infrastructure.http.problem import NOT_FOUND, OK, TOO_LARGE, UNAUTHORIZED
from aconsole.core.infrastructure.ipc.ring import EventRing
from aconsole.core.infrastructure.ipc.session import Connection
from aconsole.core.infrastructure.mcp.server import TabTokens, beside_ingest
from aconsole.core.infrastructure.mcp.tools import TabContext

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, MutableMapping

    type _Message = MutableMapping[str, object]

_HIDDEN = frozenset(
    {
        "initialize",
        "shutdown",
        "events/subscribe",
        "events/unsubscribe",
        "tab/started",
        "tab/exited",
        "tab/launchSpec",
        "terminal/screenTextReply",
    }
)
_TOKEN = "api-token"  # noqa: S105 -- fixture bearer, not a stored secret
_TAB = "tab-token"
_CONTEXT = TabContext(tab_id="t1", project_id="p1", worktree=None, agent="claude")


def test_method_openapi_matches_the_tree() -> None:
    rendered = documents()["contracts/schema/api.openapi.yaml"]
    assert rendered == Path("contracts/schema/api.openapi.yaml").read_text(encoding="utf-8")


def test_rest_methods_hide_the_socket_and_the_sidecar() -> None:
    published = {method.name for method in rest_methods()}
    assert _HIDDEN.isdisjoint(published)
    assert "app/health" in published
    assert "workSession/handoff" in published
    assert rest_method("shutdown") is None
    assert rest_method("sidecar/signal") is None
    health = rest_method("app/health")
    handoff = rest_method("workSession/handoff")
    assert health is not None
    assert rest_timeout_ms(health) == 10_000
    assert handoff is not None
    assert rest_timeout_ms(handoff) == 120_000


def test_api_token_file_is_private(tmp_path: object) -> None:
    directory = tmp_path if isinstance(tmp_path, Path) else Path(str(tmp_path))
    token = issue_api_token(directory)
    path = directory / "api.token"
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    assert path.read_text(encoding="utf-8") == f"{token}\n"
    assert os.access(path, os.R_OK)


def test_method_api_health_and_boundaries() -> None:
    status, body, timeout, called = asyncio.run(_health())
    assert status == OK
    assert json.loads(body)["database"] is False
    assert timeout == "10000"
    assert called == ["app/health"]


def test_unpublished_method_does_not_reach_the_dispatcher() -> None:
    status, called = asyncio.run(_hidden())
    assert status == NOT_FOUND
    assert called == []


def test_tab_bearer_cannot_call_methods_and_api_token_is_not_a_tab() -> None:
    api_status, mcp_reached = asyncio.run(_boundary())
    assert api_status == UNAUTHORIZED
    assert mcp_reached == []


def test_non_ascii_bearer_is_unauthorized() -> None:
    async def call(method: str, params: bytes, timeout_ms: int) -> bytes:
        _ = method, params, timeout_ms
        raise AssertionError

    non_ascii = "é"
    status, _, _ = asyncio.run(_drive(MethodApi(_TOKEN, call), "/api/v1/app/health", token=non_ascii, body=b"{}"))
    assert status == UNAUTHORIZED


def test_disconnect_discards_even_a_valid_json_prefix() -> None:
    sent = asyncio.run(_body_messages({"type": "http.disconnect"}))
    assert sent == []


def test_streamed_body_over_limit_is_rejected() -> None:
    sent = asyncio.run(_body_messages({"type": "http.request", "body": b" ", "more_body": False}))
    assert sent[0]["status"] == TOO_LARGE


async def _body_messages(last: _Message) -> list[Mapping[str, object]]:
    incoming: tuple[_Message, ...] = ({"type": "http.request", "body": b"{}", "more_body": True}, last)
    messages = iter(incoming)
    sent: list[Mapping[str, object]] = []

    async def receive() -> _Message:
        return next(messages)

    async def send(message: Mapping[str, object]) -> None:
        sent.append(message)

    async def call(method: str, params: bytes, timeout_ms: int) -> bytes:
        _ = method, params, timeout_ms
        raise AssertionError

    await MethodApi(_TOKEN, call, body_limit=2)(_scope("/api/v1/app/health", _TOKEN), receive, send)
    return sent


async def _health() -> tuple[int, bytes, str | None, list[str]]:
    called: list[str] = []
    connection = _connection()

    async def call(method: str, params: bytes, timeout_ms: int) -> bytes:
        called.append(method)
        return connection.invoke(method, params, timeout_ms=timeout_ms)

    reply = await _drive(MethodApi(_TOKEN, call), "/api/v1/app/health", token=_TOKEN, body=b"{}")
    return reply[0], reply[1], reply[2], called


async def _hidden() -> tuple[int, list[str]]:
    called: list[str] = []

    async def call(method: str, params: bytes, timeout_ms: int) -> bytes:
        called.append(method)
        _ = params, timeout_ms
        return b""

    status, _body, _timeout = await _drive(MethodApi(_TOKEN, call), "/api/v1/shutdown", token=_TOKEN, body=b"{}")
    return status, called


async def _boundary() -> tuple[int, list[str]]:
    reached: list[str] = []
    tokens = TabTokens()
    tokens.register("t1", _TAB, _CONTEXT)

    async def mcp(_scope: _Message, _receive: object, send: object) -> None:
        reached.append("mcp")
        _ = send

    async def ingest(_scope: _Message, _receive: object, send: object) -> None:
        _ = send

    async def call(method: str, params: bytes, timeout_ms: int) -> bytes:
        _ = method, params, timeout_ms
        return b""

    app = beside_ingest(ingest, mcp, tokens, MethodApi(_TOKEN, call))  # pyright: ignore[reportArgumentType] -- stand-in ASGI apps
    denied = await _drive(app, "/api/v1/git/state", token=_TAB, body=b"{}")
    _ = await _drive(app, "/mcp", token=_TOKEN, body=b"{}")
    return denied[0], reached


def _connection() -> Connection:
    connection = Connection(EventRing())
    connection.health = lambda: HealthResult(
        database=False,
        writer_queued=0,
        writer_failed=0,
        writer_last_commit_ms=0.0,
        ipc_answered=0,
        ipc_slowest_ms=0.0,
    )
    return connection


async def _drive(
    app: Callable[..., Awaitable[None]],
    path: str,
    *,
    token: str,
    body: bytes,
) -> tuple[int, bytes, str | None]:
    sent: list[_Message] = []

    async def receive() -> _Message:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: _Message) -> None:
        sent.append(message)

    await app(_scope(path, token), receive, send)
    started = sent[0]
    status = started["status"]
    raw_headers = started["headers"]
    timeout = _timeout(raw_headers)
    payload = b""
    if len(sent) > 1:
        chunk = sent[1].get("body", b"")
        if isinstance(chunk, (bytes, bytearray)):
            payload = bytes(chunk)
    if not isinstance(status, int):
        message = "status must be an integer"
        raise TypeError(message)
    return status, payload, timeout


def _scope(path: str, token: str) -> _Message:
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"authorization", f"Bearer {token}".encode("latin-1"))],
    }


def _timeout(headers: object) -> str | None:
    if not isinstance(headers, list):
        return None
    for item in headers:  # pyright: ignore[reportUnknownVariableType] -- raw ASGI header list
        if not isinstance(item, tuple) or len(item) != 2:  # pyright: ignore[reportUnknownArgumentType]
            return None
        name: object = item[0]  # pyright: ignore[reportUnknownVariableType] -- raw ASGI tuple
        value: object = item[1]  # pyright: ignore[reportUnknownVariableType] -- raw ASGI tuple
        if isinstance(name, bytes) and name.lower() == b"x-aconsole-timeout-ms" and isinstance(value, bytes):
            return value.decode("ascii")
    return None
