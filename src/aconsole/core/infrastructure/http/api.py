"""Local method API. ``POST /api/v1/{method}`` runs the same dispatcher as the Unix socket."""

from __future__ import annotations

import hmac
import os
import secrets
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Protocol, final

import msgspec

from aconsole.contracts.errors import ErrorData, ErrorObject, error_by_name
from aconsole.contracts.ipc import ResponseErr, ResponseOk
from aconsole.contracts.methods import rest_method, rest_timeout_ms
from aconsole.core.infrastructure.http.auth import bearer_token, host_allowed, origin_allowed
from aconsole.core.infrastructure.http.problem import (
    FORBIDDEN,
    NOT_FOUND,
    OK,
    HttpResponse,
    problem_response,
)
from aconsole.core.infrastructure.http.routes import MAX_BODY_BYTES
from aconsole.core.infrastructure.ipc.framing import decode_envelope

_PREFIX = "/api/v1/"
_BAD_REQUEST = 400
_CONFLICT = 409
_BUSY = 503
_INTERNAL = 500
_JSON = b"application/json"
_FILE_MODE = 0o600
_PAIR = 2
_ENCODER = msgspec.json.Encoder()
_STATUS = {
    "METHOD_NOT_FOUND": NOT_FOUND,
    "INVALID_PARAMS": _BAD_REQUEST,
    "ROLE_FORBIDDEN": FORBIDDEN,
    "SERVER_BUSY": _BUSY,
    "INTERNAL_ERROR": _INTERNAL,
}


class MethodCall(Protocol):
    """One dispatcher frame. The bytes are a JSON-RPC result or error line."""

    def __call__(self, method: str, params: bytes, timeout_ms: int) -> Awaitable[bytes]: ...


def issue_api_token(directory: Path) -> str:
    """Write ``api.token`` beside the socket. The mode stays ``0600`` after umask."""
    directory.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    path = directory / "api.token"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
    try:
        os.fchmod(descriptor, _FILE_MODE)
        _ = os.write(descriptor, f"{token}\n".encode())
    finally:
        os.close(descriptor)
    return token


@final
class MethodApi:
    """ASGI app for the method list. A tab bearer is not this token."""

    def __init__(self, token: str, call: MethodCall, *, body_limit: int = MAX_BODY_BYTES) -> None:
        if token == "":
            message = "API token must not be empty"
            raise ValueError(message)
        if body_limit < 1:
            message = "body limit must be positive"
            raise ValueError(message)
        self._token: str = token
        self._call: MethodCall = call
        self._body_limit: int = body_limit

    async def __call__(
        self,
        scope: Mapping[str, object],
        receive: Callable[[], Awaitable[Mapping[str, object]]],
        send: Callable[[Mapping[str, object]], Awaitable[None]],
    ) -> None:
        kind = scope.get("type")
        if kind == "lifespan":
            await _lifespan(receive, send)
            return
        if kind != "http":
            return
        response, timeout = await self._http(scope, receive)
        await _send(send, response, timeout)

    async def _http(
        self,
        scope: Mapping[str, object],
        receive: Callable[[], Awaitable[Mapping[str, object]]],
    ) -> tuple[HttpResponse, int | None]:
        headers = _headers(scope.get("headers"))
        refused = _refused(headers, scope.get("method"), scope.get("path"), self._token)
        if refused is not None:
            return refused
        published = rest_method(str(scope.get("path")).removeprefix(_PREFIX))
        if published is None:
            return _error_body("METHOD_NOT_FOUND"), None
        timeout = rest_timeout_ms(published)
        body = await _body_within(receive, headers, self._body_limit)
        if body is None:
            return _problem("MESSAGE_TOO_LARGE", "api-body"), timeout
        return _from_frame(await self._call(published.name, body, timeout)), timeout


def _refused(
    headers: dict[str, str], method: object, path: object, token: str
) -> tuple[HttpResponse, None] | None:
    if not origin_allowed(headers.get("origin")) or not host_allowed(headers.get("host")):
        return _problem("INGEST_ORIGIN_FORBIDDEN", "api-origin"), None
    if not _token_matches(headers.get("authorization"), token):
        return _problem("INGEST_UNAUTHORIZED", "api-token"), None
    if _method_name(method, path) is None:
        return _error_body("METHOD_NOT_FOUND"), None
    return None


async def _body_within(
    receive: Callable[[], Awaitable[Mapping[str, object]]],
    headers: dict[str, str],
    limit: int,
) -> bytes | None:
    declared = _content_length(headers.get("content-length"))
    if declared is not None and declared > limit:
        return None
    return await _read_body(receive, limit=limit)


def _token_matches(header: str | None, expected: str) -> bool:
    presented = bearer_token(header)
    if presented is None:
        return False
    return hmac.compare_digest(presented, expected)


def _method_name(method: object, path: object) -> str | None:
    if method != "POST" or not isinstance(path, str) or not path.startswith(_PREFIX):
        return None
    name = path.removeprefix(_PREFIX)
    if name == "" or name.endswith("/") or "//" in name:
        return None
    return name


def _from_frame(frame: bytes) -> HttpResponse:
    envelope = decode_envelope(frame)
    if isinstance(envelope, ResponseOk):
        return HttpResponse(status=OK, body=bytes(envelope.result), content_type=_JSON)
    if isinstance(envelope, ResponseErr):
        name = envelope.error.message
        status = _STATUS.get(name, _CONFLICT)
        return HttpResponse(status=status, body=_ENCODER.encode(envelope.error), content_type=_JSON)
    return _error_body("INTERNAL_ERROR")


def _error_body(name: str) -> HttpResponse:
    """A registry error without asking the dispatcher. Used when the path is not a published method."""
    spec = error_by_name(name)
    error = ErrorObject(
        code=spec.code,
        message=spec.name,
        data=ErrorData(
            reason=spec.name,
            domain="aconsole.ipc",
            retryable=spec.retryable,
            user_message_key=f"error.{spec.name.lower()}",
        ),
    )
    status = _STATUS.get(name, _CONFLICT)
    return HttpResponse(status=status, body=_ENCODER.encode(error), content_type=_JSON)


def _problem(name: str, instance: str) -> HttpResponse:
    details = {
        "INGEST_UNAUTHORIZED": "Bearer token is missing or unknown",
        "INGEST_ORIGIN_FORBIDDEN": "Origin is not local",
        "MESSAGE_TOO_LARGE": "JSON body exceeds 1 MiB",
    }
    return problem_response(name=name, detail=details[name], instance=instance)


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
        if key not in found:
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


async def _read_body(
    receive: Callable[[], Awaitable[Mapping[str, object]]], *, limit: int
) -> bytes | None:
    parts: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            return b"".join(parts)
        chunk = message.get("body", b"")
        data = b"" if not isinstance(chunk, (bytes, bytearray)) else bytes(chunk)
        total += len(data)
        if total > limit:
            return None
        parts.append(data)
        if message.get("more_body") is not True:
            return b"".join(parts)


async def _lifespan(
    receive: Callable[[], Awaitable[Mapping[str, object]]],
    send: Callable[[Mapping[str, object]], Awaitable[None]],
) -> None:
    while True:
        message = await receive()
        kind = message.get("type")
        if kind == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
            continue
        if kind == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
        return


async def _send(
    send: Callable[[Mapping[str, object]], Awaitable[None]],
    response: HttpResponse,
    timeout_ms: int | None,
) -> None:
    headers: list[tuple[bytes, bytes]] = [(b"content-length", str(len(response.body)).encode("ascii"))]
    if response.content_type is not None:
        headers.insert(0, (b"content-type", response.content_type))
    if timeout_ms is not None:
        headers.append((b"x-aconsole-timeout-ms", str(timeout_ms).encode("ascii")))
    await send({"type": "http.response.start", "status": response.status, "headers": headers})
    await send({"type": "http.response.body", "body": response.body})
