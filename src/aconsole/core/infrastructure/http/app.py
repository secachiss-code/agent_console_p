"""ASGI ingest app. Uvicorn serves it on 127.0.0.1."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Protocol, final

import uvicorn

from aconsole.contracts import API_VERSION
from aconsole.core.infrastructure.http.auth import Decision, TokenBook, origin_allowed
from aconsole.core.infrastructure.http.problem import (
    HttpResponse,
    empty_json,
    health_json,
    json_response,
    no_content,
    problem_response,
)
from aconsole.core.infrastructure.http.routes import MAX_BODY_BYTES, IngestItem, IngestQueue, Route, classify, match

ACK_BUDGET_S = 0.05
_HOST = "127.0.0.1"
_PAIR = 2
_LOG = logging.getLogger(__name__)
_NO_CONTENT_KINDS = frozenset({"claude.statusline", "codex.notify"})

_DETAIL = {
    "INGEST_UNAUTHORIZED": "Bearer token is missing or unknown",
    "INGEST_TAB_MISMATCH": "Token is not valid for this tab",
    "INGEST_ORIGIN_FORBIDDEN": "Origin is not local",
    "TAB_NOT_FOUND": "No such route",
    "MESSAGE_TOO_LARGE": "JSON body exceeds 1 MiB",
}


class Observer(Protocol):
    async def __call__(self, item: IngestItem) -> None: ...


class Receive(Protocol):
    async def __call__(self) -> Mapping[str, object]: ...


class Send(Protocol):
    async def __call__(self, message: Mapping[str, object]) -> None: ...


@final
class IngestApp:
    """HTTP ingest. Observational hooks return before the observer runs."""

    def __init__(
        self,
        book: TokenBook,
        queue: IngestQueue,
        *,
        version: str = API_VERSION,
        body_limit: int = MAX_BODY_BYTES,
        observer: Observer | None = None,
    ) -> None:
        if body_limit < 1:
            message = "body limit must be positive"
            raise ValueError(message)
        self._book = book
        self._queue = queue
        self._version = version
        self._body_limit = body_limit
        self._observer = observer
        self._ids = 0
        self._invalid = 0
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def invalid_payloads(self) -> int:
        return self._invalid

    def pending(self) -> tuple[asyncio.Task[None], ...]:
        return tuple(self._tasks)

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        kind = scope.get("type")
        if kind == "lifespan":
            await _lifespan(receive, send)
            return
        if kind != "http":
            return
        response = await self._http(scope, receive)
        await _send(send, response)

    async def _http(self, scope: Mapping[str, object], receive: Receive) -> HttpResponse:
        instance = self._next_id()
        headers = _headers(scope.get("headers"))
        if not origin_allowed(headers.get("origin")):
            return _problem("INGEST_ORIGIN_FORBIDDEN", instance)
        method = scope.get("method")
        path = scope.get("path")
        if not isinstance(method, str) or not isinstance(path, str):
            return _problem("TAB_NOT_FOUND", instance)
        route = match(method, path)
        if route is None:
            return _problem("TAB_NOT_FOUND", instance)
        if route.kind == "health":
            return json_response(health_json(self._version))
        return await self._tab(route, headers, receive, instance)

    async def _tab(
        self,
        route: Route,
        headers: dict[str, str],
        receive: Receive,
        instance: str,
    ) -> HttpResponse:
        refused = self._refused(route.tab_id, headers, instance)
        if refused is not None:
            return refused
        declared = _content_length(headers.get("content-length"))
        if declared is not None and declared > self._body_limit:
            return _problem("MESSAGE_TOO_LARGE", instance)
        body = await _read_body(receive, limit=self._body_limit)
        if body is None:
            return _problem("MESSAGE_TOO_LARGE", instance)
        return self._accept(route, body)

    def _refused(self, tab_id: str, headers: dict[str, str], instance: str) -> HttpResponse | None:
        decision = self._book.check(headers.get("authorization"), tab_id)
        if decision is Decision.MISMATCH:
            return _problem("INGEST_TAB_MISMATCH", instance)
        if decision is not Decision.OK:
            return _problem("INGEST_UNAUTHORIZED", instance)
        return None

    def _accept(self, route: Route, body: bytes) -> HttpResponse:
        if classify(route.kind, body) is None:
            self._invalid += 1
            _LOG.info("ingest payload invalid for %s", route.tab_id)
            return json_response(empty_json())
        item = IngestItem(tab_id=route.tab_id, kind=route.kind, body=body)
        self._queue.push(item)
        self._schedule(item)
        if route.kind in _NO_CONTENT_KINDS:
            return no_content()
        return json_response(empty_json())

    def _next_id(self) -> str:
        self._ids += 1
        return f"ingest-{self._ids}"

    def _schedule(self, item: IngestItem) -> None:
        observer = self._observer
        if observer is None:
            return
        task = asyncio.create_task(self._run(observer, item))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, observer: Observer, item: IngestItem) -> None:
        try:
            await observer(item)
        except Exception:  # noqa: BLE001
            _LOG.exception("ingest handler failed for %s", item.tab_id)


def serve_config(app: object, *, port: int, host: str = _HOST) -> uvicorn.Config:
    """Localhost uvicorn config. The caller chooses the port."""
    return uvicorn.Config(
        app,  # pyright: ignore[reportArgumentType] -- uvicorn accepts the combined ASGI callable beside Starlette
        host=host,
        port=port,
        access_log=False,
        log_level="warning",
        lifespan="on",
    )


def _problem(name: str, instance: str) -> HttpResponse:
    return problem_response(name=name, detail=_DETAIL[name], instance=instance)


def _headers(raw: object) -> dict[str, str]:
    found: dict[str, str] = {}
    if not isinstance(raw, list):
        return found
    for item in raw:  # pyright: ignore[reportUnknownVariableType] -- isinstance(list) yields Unknown
        pair = _bytes_pair(item)  # pyright: ignore[reportUnknownArgumentType] -- isinstance(list) yields Unknown
        if pair is None:
            continue
        name, value = pair
        key = name.decode("latin-1").lower()
        if key in found:
            continue
        found[key] = value.decode("latin-1")
    return found


def _bytes_pair(item: object) -> tuple[bytes, bytes] | None:
    if not isinstance(item, tuple) or len(item) != _PAIR:  # pyright: ignore[reportUnknownArgumentType] -- tuple items are Unknown
        return None
    name: object = item[0]  # pyright: ignore[reportUnknownVariableType] -- tuple items are Unknown
    value: object = item[1]  # pyright: ignore[reportUnknownVariableType] -- tuple items are Unknown
    if not isinstance(name, (bytes, bytearray)) or not isinstance(value, (bytes, bytearray)):
        return None
    return bytes(name), bytes(value)


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed


async def _read_body(receive: Receive, *, limit: int) -> bytes | None:
    parts: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        kind = message.get("type")
        if kind != "http.request":
            return b"".join(parts)
        chunk = message.get("body", b"")
        data = b"" if not isinstance(chunk, (bytes, bytearray)) else bytes(chunk)
        total += len(data)
        if total > limit:
            return None
        parts.append(data)
        if message.get("more_body") is not True:
            return b"".join(parts)


async def _lifespan(receive: Receive, send: Send) -> None:
    while True:
        message = await receive()
        kind = message.get("type")
        if kind == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
            continue
        if kind == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
        return


async def _send(send: Send, response: HttpResponse) -> None:
    headers: list[tuple[bytes, bytes]] = [(b"content-length", str(len(response.body)).encode("ascii"))]
    if response.content_type is not None:
        headers.insert(0, (b"content-type", response.content_type))
    started: dict[str, object] = {
        "type": "http.response.start",
        "status": response.status,
        "headers": headers,
    }
    await send(started)
    await send({"type": "http.response.body", "body": response.body})
