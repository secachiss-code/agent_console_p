"""Ingest HTTP: auth, origin, fast ack, queue overflow, OpenAPI conformance."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import jsonschema
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aconsole.contracts import API_VERSION
from aconsole.contracts.generate import documents
from aconsole.core.infrastructure.http.app import ACK_BUDGET_S, IngestApp, serve_config
from aconsole.core.infrastructure.http.auth import TokenBook
from aconsole.core.infrastructure.http.problem import FORBIDDEN, NOT_FOUND, OK, TOO_LARGE, UNAUTHORIZED
from aconsole.core.infrastructure.http.routes import MAX_BODY_BYTES, QUEUE_LIMIT, IngestItem, IngestQueue, classify

if TYPE_CHECKING:
    from collections.abc import Mapping

_CLAUDE = b'{"session_id":"s","hook_event_name":"Notification"}'
_STATUSLINE = b'{"session_id":"s"}'
_NOTIFY = b'{"type":"agent-turn-complete"}'
_CODEX = b'{"session_id":"s","hook_event_name":"Stop"}'
_OPENAPI = "contracts/schema/ingest.openapi.yaml"
_METHODS = frozenset({"get", "post"})


@dataclass(frozen=True, slots=True)
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes
    consumed: bool


def test_ingest_http_limits_are_the_contract() -> None:
    queue = IngestQueue()
    assert queue.depth == 0
    assert QUEUE_LIMIT == 10_000
    assert MAX_BODY_BYTES == 1024 * 1024
    assert ACK_BUDGET_S == 0.05
    with pytest.raises(ValueError, match="positive"):
        IngestQueue(limit=0)


def test_ingest_http_queue_overflow_coalesces_by_tab() -> None:
    queue = IngestQueue(limit=1)
    first = IngestItem("a", "claude.hook", b"1")
    second = IngestItem("a", "claude.hook", b"2")
    third = IngestItem("b", "claude.hook", b"3")
    queue.push(first)
    queue.push(second)
    queue.push(third)
    assert queue.depth == 1
    assert queue.lost("a") == 1
    assert queue.lost("b") == 1
    assert queue.needs_resync("a") is True
    assert queue.needs_resync("b") is True
    assert queue.coalesced("a") == second
    assert queue.coalesced("b") == third
    assert queue.lost("missing") == 0
    assert queue.needs_resync("missing") is False
    assert queue.coalesced("missing") is None


def test_ingest_http_rejects_blank_tab_and_body_limit() -> None:
    book = TokenBook()
    with pytest.raises(ValueError, match="path segment"):
        book.issue("")
    with pytest.raises(ValueError, match="path segment"):
        book.issue("a/b")
    with pytest.raises(ValueError, match="body limit"):
        IngestApp(book, IngestQueue(), body_limit=0)
    book.drop("absent")
    issued = book.issue("tab-1")
    suggestions = (
        b'{"session_id":"s","hook_event_name":"PermissionRequest","tool_name":"Bash",'
        b'"tool_input":{"command":"ls"},"permission_suggestions":[{"type":"addRules"}]}'
    )
    tool = b'{"session_id":"s","hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{}}'
    assert issued != ""
    assert classify("health", b"{}") is None
    assert classify("claude.hook", suggestions) is not None
    assert classify("claude.hook", tool) is not None


@pytest.mark.parametrize(
    ("origin", "status"),
    [
        (None, OK),
        ("null", OK),
        ("http://127.0.0.1:9", OK),
        ("https://LOCALHOST", OK),
        ("http://[::1]", OK),
        ("http://evil.test", FORBIDDEN),
        ("", FORBIDDEN),
        ("http://127.0.0.1.evil.test", FORBIDDEN),
    ],
)
def test_ingest_http_origin(origin: str | None, status: int) -> None:
    got, reason = asyncio.run(_origin(origin))
    assert got == status
    if status == FORBIDDEN:
        assert reason == "INGEST_ORIGIN_FORBIDDEN"


def test_ingest_http_status_codes() -> None:
    status, reasons = asyncio.run(_statuses())
    assert status[0] == UNAUTHORIZED
    assert status[1] == FORBIDDEN
    assert status[2] == NOT_FOUND
    assert status[3] == TOO_LARGE
    assert reasons == (
        "INGEST_UNAUTHORIZED",
        "INGEST_TAB_MISMATCH",
        "TAB_NOT_FOUND",
        "MESSAGE_TOO_LARGE",
    )


def test_ingest_http_auth_and_payloads() -> None:
    invalid, depth, codes = asyncio.run(_payloads())
    assert invalid == 2
    assert depth == 3
    assert codes == (OK, 204, 204)


def test_ingest_http_events_and_ack() -> None:
    depth, elapsed, ran = asyncio.run(_events_and_ack())
    assert depth == 10
    assert ran >= 1
    assert elapsed < ACK_BUDGET_S


def test_ingest_http_overflow_on_the_wire() -> None:
    lost, resync, depth, statuses = asyncio.run(_wire_overflow())
    assert lost == 1
    assert resync is True
    assert depth == 1
    assert statuses == (OK, OK)


def test_ingest_http_observer_error(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR):
        status = asyncio.run(_boom())
    assert status == OK
    assert "ingest handler failed" in caplog.text


def test_ingest_http_lifespan_and_odd_scopes() -> None:
    lifespan, websocket, bad_method, health = asyncio.run(_odd_scopes())
    assert lifespan == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert websocket == []
    assert bad_method == NOT_FOUND
    assert health == OK


def test_ingest_http_framing_edges() -> None:
    consumed, status, chunked, duplicate = asyncio.run(_framing())
    assert consumed is False
    assert status == TOO_LARGE
    assert chunked == OK
    assert duplicate == OK


def test_ingest_http_body_and_path_edges() -> None:
    overflow, bad_length, tail, empty, bare = asyncio.run(_more_edges())
    assert overflow == TOO_LARGE
    assert bad_length == OK
    assert tail == NOT_FOUND
    assert empty == NOT_FOUND
    assert bare == NOT_FOUND


def test_ingest_http_uvicorn_config() -> None:
    app = IngestApp(TokenBook(), IngestQueue())
    config = serve_config(app, port=9)
    assert config.host == "127.0.0.1"
    assert config.port == 9
    assert config.access_log is False


def test_ingest_http_openapi_is_generated() -> None:
    rendered = documents()[_OPENAPI]
    assert rendered == Path(_OPENAPI).read_text(encoding="utf-8")


def test_ingest_http_openapi_every_operation() -> None:
    failures = asyncio.run(_exercise(_document()))
    assert failures == []


@settings(max_examples=20, deadline=None)
@given(body=st.binary(max_size=80), mode=st.sampled_from(["ok", "anon", "evil"]))
def test_ingest_http_openapi_fuzz(body: bytes, mode: str) -> None:
    documented = _response_codes("/v1/tabs/{tab_id}/claude/hook", "post")
    status = asyncio.run(_fuzz(body, mode=mode))
    assert status in documented


def _document() -> dict[str, object]:
    parsed: object = json.loads(Path(_OPENAPI).read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        message = "openapi root must be an object"
        raise TypeError(message)
    return parsed


def _response_codes(path: str, method: str) -> set[int]:
    paths = _document().get("paths")
    if not isinstance(paths, dict):
        return set()
    item = paths.get(path)
    if not isinstance(item, dict):
        return set()
    operation = item.get(method)
    if not isinstance(operation, dict):
        return set()
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return set()
    return {int(code) for code in responses}


async def _origin(origin: str | None) -> tuple[int, str]:
    app = IngestApp(TokenBook(), IngestQueue())
    reply = await _drive(app, "GET", "/v1/health", origin=origin)
    if reply.status == OK:
        assert json.loads(reply.body) == {"ok": True, "version": API_VERSION}
        return reply.status, ""
    return reply.status, _reason(reply.body)


async def _statuses() -> tuple[list[int], tuple[str, ...]]:
    book = TokenBook()
    first = book.issue("tab-1")
    second = book.issue("tab-2")
    app = IngestApp(book, IngestQueue())
    path = "/v1/tabs/tab-1/claude/hook"
    missing = await _drive(app, "POST", path, body=_CLAUDE)
    mismatch = await _drive(app, "POST", path, token=second, body=_CLAUDE)
    unknown = await _drive(app, "POST", "/v1/missing", token=first, body=_CLAUDE)
    huge = await _drive(app, "POST", path, token=first, body=_CLAUDE, content_length=MAX_BODY_BYTES + 1)
    replies = (missing, mismatch, unknown, huge)
    return [item.status for item in replies], tuple(_reason(item.body) for item in replies)


async def _payloads() -> tuple[int, int, tuple[int, int, int]]:
    book = TokenBook()
    token = book.issue("tab-1")
    queue = IngestQueue()
    app = IngestApp(book, queue)
    path = "/v1/tabs/tab-1/claude/hook"
    invalid = await _drive(app, "POST", path, token=token, body=b"not-json")
    missing_tool = await _drive(
        app,
        "POST",
        path,
        token=token,
        body=b'{"session_id":"s","hook_event_name":"PermissionRequest"}',
    )
    extra = await _drive(
        app,
        "POST",
        path,
        token=token,
        body=b'{"session_id":"s","hook_event_name":"Stop","mystery":true}',
    )
    line = await _drive(app, "POST", "/v1/tabs/tab-1/claude/statusline", token=token, body=_STATUSLINE)
    note = await _drive(app, "POST", "/v1/tabs/tab-1/codex/notify", token=token, body=_NOTIFY)
    assert invalid.body == b"{}"
    assert missing_tool.body == b"{}"
    assert line.status == 204
    assert line.body == b""
    assert "content-type" not in line.headers
    assert note.status == 204
    book.drop("tab-1")
    dropped = await _drive(app, "POST", path, token=token, body=_CLAUDE)
    basic = await _drive(app, "POST", path, token=None, body=_CLAUDE, authorization="Basic secret")
    assert dropped.status == UNAUTHORIZED
    assert basic.status == UNAUTHORIZED
    return app.invalid_payloads, queue.depth, (extra.status, line.status, note.status)


async def _events_and_ack() -> tuple[int, float, int]:
    book = TokenBook()
    token = book.issue("tab-1")
    queue = IngestQueue()
    started: list[float] = []

    async def observer(_item: IngestItem) -> None:
        started.append(asyncio.get_running_loop().time())
        await asyncio.sleep(0.2)

    app = IngestApp(book, queue, observer=observer)
    bodies = (
        _CLAUDE,
        b'{"session_id":"s","hook_event_name":"Stop"}',
        b'{"session_id":"s","hook_event_name":"StopFailure","error":"rate_limit"}',
        b'{"session_id":"s","hook_event_name":"SessionStart","source":"startup"}',
        b'{"session_id":"s","hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{}}',
        b'{"session_id":"s","hook_event_name":"PostToolUse","tool_name":"Bash"}',
        b'{"session_id":"s","hook_event_name":"SessionEnd"}',
        b'{"session_id":"s","hook_event_name":"PermissionRequest","tool_name":"Bash","tool_input":{"command":"ls"}}',
        b'{"session_id":"s","hook_event_name":"PermissionRequest","tool_name":"Bash","tool_input":{"command":"ls"}}',
    )
    began = asyncio.get_running_loop().time()
    reply = await _drive(app, "POST", "/v1/tabs/tab-1/claude/hook", token=token, body=bodies[-1])
    elapsed = asyncio.get_running_loop().time() - began
    for body in bodies[:-1]:
        await _drive(app, "POST", "/v1/tabs/tab-1/claude/hook", token=token, body=body)
    codex = await _drive(
        app,
        "POST",
        "/v1/tabs/tab-1/codex/hook",
        token=token,
        body=b'{"session_id":"s","hook_event_name":"PermissionRequest","tool_name":"Bash","tool_input":{}}',
    )
    assert reply.status == OK
    assert reply.body == b"{}"
    assert codex.status == OK
    await asyncio.sleep(0)
    ran = len(started)
    for task in app.pending():
        _ = task.cancel()
    await asyncio.gather(*app.pending(), return_exceptions=True)
    return queue.depth, elapsed, ran


async def _more_edges() -> tuple[int, int, int, int, int]:
    book = TokenBook()
    token = book.issue("tab-1")
    path = "/v1/tabs/tab-1/claude/hook"
    tight = IngestApp(book, IngestQueue(), body_limit=4)
    overflow = await _drive(
        tight,
        "POST",
        path,
        token=token,
        content_length=4,
        raw_messages=[{"type": "http.request", "body": b"12345", "more_body": False}],
    )
    wide = IngestApp(book, IngestQueue())
    raw = [(b"authorization", f"Bearer {token}".encode()), (b"content-length", b"nope")]
    bad_length = await _drive(wide, "POST", path, raw_headers=raw, body=_CLAUDE)
    tail = await _drive(wide, "POST", "/v1/tabs/tab-1/nope", token=token)
    empty = await _drive(wide, "POST", "/v1/tabs//claude/hook", token=token)
    bare = await _drive(wide, "POST", "/v1/tabs/tab-1", token=token)
    return overflow.status, bad_length.status, tail.status, empty.status, bare.status


async def _wire_overflow() -> tuple[int, bool, int, tuple[int, int]]:
    book = TokenBook()
    token = book.issue("tab-1")
    queue = IngestQueue(limit=1)
    app = IngestApp(book, queue)
    path = "/v1/tabs/tab-1/claude/hook"
    first = await _drive(app, "POST", path, token=token, body=_CLAUDE)
    second = await _drive(app, "POST", path, token=token, body=_CODEX)
    kept = queue.coalesced("tab-1")
    assert kept is not None
    assert kept.body == _CODEX
    return queue.lost("tab-1"), queue.needs_resync("tab-1"), queue.depth, (first.status, second.status)


async def _boom() -> int:
    book = TokenBook()
    token = book.issue("tab-1")

    async def observer(_item: IngestItem) -> None:
        message = "observer failed"
        raise RuntimeError(message)

    app = IngestApp(book, IngestQueue(), observer=observer)
    reply = await _drive(app, "POST", "/v1/tabs/tab-1/claude/hook", token=token, body=_CLAUDE)
    await asyncio.sleep(0)
    await asyncio.gather(*app.pending(), return_exceptions=True)
    return reply.status


async def _odd_scopes() -> tuple[list[str], list[str], int, int]:
    app = IngestApp(TokenBook(), IngestQueue())
    sent: list[str] = []

    async def receive() -> Mapping[str, object]:
        if not receive.messages:
            return {"type": "lifespan.shutdown"}
        return receive.messages.pop(0)

    receive.messages = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]

    async def send(message: Mapping[str, object]) -> None:
        kind = message.get("type")
        if isinstance(kind, str):
            sent.append(kind)

    await app({"type": "lifespan"}, receive, send)
    receive.messages = [{"type": "lifespan.other"}]
    await app({"type": "lifespan"}, receive, send)
    websocket: list[str] = []

    async def unused_receive() -> Mapping[str, object]:
        return {"type": "websocket.connect"}

    async def unused_send(_message: Mapping[str, object]) -> None:
        websocket.append("sent")

    await app({"type": "websocket"}, unused_receive, unused_send)
    bad = await app_status(app, {"type": "http", "method": 1, "path": "/v1/health", "headers": []})
    health = await app_status(app, {"type": "http", "method": "GET", "path": "/v1/health"})
    return sent, websocket, bad, health


async def _framing() -> tuple[bool, int, int, int]:
    book = TokenBook()
    token = book.issue("tab-1")
    app = IngestApp(book, IngestQueue(), body_limit=8)
    path = "/v1/tabs/tab-1/claude/hook"
    skipped: list[bool] = []

    async def receive() -> Mapping[str, object]:
        skipped.append(True)
        return {"type": "http.request", "body": _CLAUDE, "more_body": False}

    async def send(message: Mapping[str, object]) -> None:
        send.messages.append(message)

    send.messages = []
    headers = [
        (b"authorization", f"Bearer {token}".encode()),
        (b"content-length", b"999"),
    ]
    await app({"type": "http", "method": "POST", "path": path, "headers": headers}, receive, send)
    status = _status_of(send.messages)
    wide = IngestApp(book, IngestQueue())
    valid = await _drive(wide, "POST", path, token=token, raw_messages=_stop_frames())
    disconnected = await _drive(
        wide,
        "POST",
        path,
        token=token,
        raw_messages=[{"type": "http.disconnect"}],
    )
    replaced = book.issue("tab-1")
    assert replaced != token
    stale = await _drive(wide, "POST", path, token=token, body=_CLAUDE)
    odd_headers = [
        (b"authorization",),
        ("authorization", b"Bearer x"),
        (b"authorization", f"Bearer {replaced}".encode()),
        (b"authorization", b"Bearer stale"),
    ]
    first_wins = await _drive(wide, "POST", path, raw_headers=odd_headers, body=_CLAUDE)
    assert stale.status == UNAUTHORIZED
    assert disconnected.status == OK
    assert disconnected.body == b"{}"
    return bool(skipped), status, valid.status, first_wins.status


def _stop_frames() -> list[Mapping[str, object]]:
    return [
        {"type": "http.request", "body": b'{"session_id":"s",', "more_body": True},
        {"type": "http.request", "body": 7, "more_body": True},
        {"type": "http.request", "body": b'"hook_event_name":"Stop"}', "more_body": False},
    ]


async def app_status(app: IngestApp, scope: Mapping[str, object]) -> int:
    sent: list[Mapping[str, object]] = []

    async def receive() -> Mapping[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Mapping[str, object]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return _status_of(sent)


def _status_of(messages: list[Mapping[str, object]]) -> int:
    for message in messages:
        if message.get("type") == "http.response.start":
            status = message.get("status")
            if isinstance(status, int):
                return status
    return 0


def _reason(body: bytes) -> str:
    parsed: object = json.loads(body)
    if not isinstance(parsed, dict):
        return ""
    reason = parsed.get("reason")
    if not isinstance(reason, str):
        return ""
    return reason


async def _exercise(document: dict[str, object]) -> list[str]:
    failures: list[str] = []
    paths = document.get("paths")
    schemas = _schemas(document)
    if not isinstance(paths, dict):
        return ["paths missing"]
    book = TokenBook()
    token = book.issue("tab-1")
    other = book.issue("tab-2")
    app = IngestApp(book, IngestQueue())
    samples = {
        "/v1/health": b"",
        "/v1/tabs/{tab_id}/claude/hook": _CLAUDE,
        "/v1/tabs/{tab_id}/claude/statusline": _STATUSLINE,
        "/v1/tabs/{tab_id}/codex/hook": _CODEX,
        "/v1/tabs/{tab_id}/codex/notify": _NOTIFY,
    }
    seen: set[tuple[str, str]] = set()
    for path, item in paths.items():
        if not isinstance(path, str) or not isinstance(item, dict):
            failures.append("path item is not an object")
            continue
        for method, operation in item.items():
            if not isinstance(method, str) or method not in _METHODS or not isinstance(operation, dict):
                continue
            seen.add((method, path))
            sample = samples.get(path)
            if sample is None:
                failures.append(f"no sample for {method} {path}")
                continue
            concrete = path.replace("{tab_id}", "tab-1")
            success = await _drive(app, method.upper(), concrete, token=token, body=sample)
            _check(failures, operation, success, schemas, f"{method} {path} success")
            if method == "get":
                foreign = await _drive(app, "GET", concrete, origin="http://evil.test")
                _check(failures, operation, foreign, schemas, f"{method} {path} origin")
                continue
            denied = await _drive(app, "POST", concrete, body=sample)
            mismatched = await _drive(app, "POST", concrete, token=other, body=sample)
            foreign = await _drive(app, "POST", concrete, token=token, origin="https://evil.test", body=sample)
            huge = await _drive(
                app,
                "POST",
                concrete,
                token=token,
                body=sample,
                content_length=MAX_BODY_BYTES + 1,
            )
            _check(failures, operation, denied, schemas, f"{method} {path} auth")
            _check(failures, operation, mismatched, schemas, f"{method} {path} mismatch")
            _check(failures, operation, foreign, schemas, f"{method} {path} origin")
            _check(failures, operation, huge, schemas, f"{method} {path} size")
    if ("post", "/v1/tabs/{tab_id}/claude/hook") not in seen:
        failures.append("claude hook is not in the document")
    missing = await _drive(app, "GET", "/v1/tabs/tab-1/claude/hook", token=token)
    if missing.status != NOT_FOUND:
        failures.append(f"wrong method status {missing.status}")
    return failures


def _check(
    failures: list[str],
    operation: dict[str, object],
    reply: Reply,
    schemas: dict[str, object],
    label: str,
) -> None:
    responses = operation.get("responses")
    if not isinstance(responses, dict) or str(reply.status) not in responses:
        failures.append(f"{label} status {reply.status} is not documented")
        return
    spec = responses[str(reply.status)]
    if not isinstance(spec, dict):
        failures.append(f"{label} response is not an object")
        return
    content = spec.get("content")
    if content is None:
        if reply.body != b"" or "content-type" in reply.headers:
            failures.append(f"{label} should be empty")
        return
    if not isinstance(content, dict):
        failures.append(f"{label} content is not an object")
        return
    media = reply.headers.get("content-type")
    schema = content.get(media) if media is not None else None
    if not isinstance(schema, dict):
        failures.append(f"{label} content-type {media}")
        return
    body_schema = schema.get("schema")
    try:
        instance: object = json.loads(reply.body)
    except json.JSONDecodeError:
        failures.append(f"{label} body is not json")
        return
    try:
        jsonschema.Draft202012Validator(_deref(body_schema, schemas)).validate(instance)
    except jsonschema.ValidationError as exc:
        failures.append(f"{label} schema: {exc.message}")


def _schemas(document: dict[str, object]) -> dict[str, object]:
    components = document.get("components")
    if not isinstance(components, dict):
        return {}
    schemas = components.get("schemas")
    if not isinstance(schemas, dict):
        return {}
    return schemas


def _deref(schema: object, schemas: dict[str, object]) -> object:
    if isinstance(schema, dict):
        ref = schema.get("$ref")
        if isinstance(ref, str):
            name = ref.rsplit("/", maxsplit=1)[-1]
            target = schemas.get(name)
            if isinstance(target, dict):
                return _deref(target, schemas)
            return schema
        return {key: _deref(value, schemas) for key, value in schema.items()}
    if isinstance(schema, list):
        return [_deref(item, schemas) for item in schema]
    return schema


async def _fuzz(body: bytes, mode: str) -> int:
    book = TokenBook()
    token = book.issue("tab-1")
    app = IngestApp(book, IngestQueue())
    origin = "http://evil.test" if mode == "evil" else None
    presented = token if mode == "ok" else None
    reply = await _drive(
        app,
        "POST",
        "/v1/tabs/tab-1/claude/hook",
        token=presented,
        origin=origin,
        body=body,
    )
    return reply.status


def _request_headers(
    *,
    token: str | None,
    origin: str | None,
    body: bytes,
    content_length: int | None,
    authorization: str | None,
) -> list[tuple[object, ...]]:
    headers: list[tuple[object, ...]] = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    elif token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    elif body:
        headers.append((b"content-length", str(len(body)).encode("ascii")))
    return headers


async def _drive(
    app: IngestApp,
    method: str,
    path: str,
    *,
    token: str | None = None,
    origin: str | None = None,
    body: bytes = b"",
    content_length: int | None = None,
    authorization: str | None = None,
    raw_headers: list[tuple[object, ...]] | None = None,
    raw_messages: list[Mapping[str, object]] | None = None,
) -> Reply:
    headers = raw_headers if raw_headers is not None else _request_headers(
        token=token,
        origin=origin,
        body=body,
        content_length=content_length,
        authorization=authorization,
    )
    frames = list(raw_messages) if raw_messages is not None else None
    if frames is None:
        frames = [{"type": "http.request", "body": body, "more_body": False}]
    consumed = False

    async def receive() -> Mapping[str, object]:
        nonlocal consumed
        consumed = True
        if not frames:
            return {"type": "http.request", "body": b"", "more_body": False}
        return frames.pop(0)

    sent: list[Mapping[str, object]] = []

    async def send(message: Mapping[str, object]) -> None:
        sent.append(message)

    scope: dict[str, object] = {"type": "http", "method": method, "path": path, "headers": headers}
    await app(scope, receive, send)
    return Reply(_status_of(sent), _response_headers(sent), _response_body(sent), consumed)


def _response_headers(messages: list[Mapping[str, object]]) -> dict[str, str]:
    found: dict[str, str] = {}
    for message in messages:
        if message.get("type") != "http.response.start":
            continue
        raw = message.get("headers")
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            name, value = item
            if isinstance(name, bytes) and isinstance(value, bytes):
                found[name.decode("latin-1")] = value.decode("latin-1")
    return found


def _response_body(messages: list[Mapping[str, object]]) -> bytes:
    chunks: list[bytes] = []
    for message in messages:
        if message.get("type") != "http.response.body":
            continue
        chunk = message.get("body", b"")
        if isinstance(chunk, bytes):
            chunks.append(chunk)
    return b"".join(chunks)
