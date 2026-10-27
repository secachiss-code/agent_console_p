"""RFC 9457 problem bodies for the ingest HTTP API."""

from __future__ import annotations

from dataclasses import dataclass

import msgspec

from aconsole.contracts.errors import error_by_name
from aconsole.contracts.ingest import EmptyObject, Health, Problem

OK = 200
NO_CONTENT = 204
UNAUTHORIZED = 401
FORBIDDEN = 403
NOT_FOUND = 404
TOO_LARGE = 413

JSON = b"application/json"
PROBLEM = b"application/problem+json"

_TITLES = {
    "INGEST_UNAUTHORIZED": "Unauthorized",
    "INGEST_TAB_MISMATCH": "Tab mismatch",
    "INGEST_ORIGIN_FORBIDDEN": "Origin forbidden",
    "TAB_NOT_FOUND": "Not found",
    "MESSAGE_TOO_LARGE": "Payload too large",
}

_STATUS = {
    "INGEST_UNAUTHORIZED": UNAUTHORIZED,
    "INGEST_TAB_MISMATCH": FORBIDDEN,
    "INGEST_ORIGIN_FORBIDDEN": FORBIDDEN,
    "TAB_NOT_FOUND": NOT_FOUND,
    "MESSAGE_TOO_LARGE": TOO_LARGE,
}

_ENCODER = msgspec.json.Encoder()
_EMPTY = _ENCODER.encode(EmptyObject())


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes
    content_type: bytes | None


def empty_json() -> bytes:
    return _EMPTY


def health_json(version: str) -> bytes:
    return _ENCODER.encode(Health(ok=True, version=version))


def problem_response(*, name: str, detail: str, instance: str) -> HttpResponse:
    """One problem+json body. `name` is a registry error, `instance` is the request id."""
    status = _STATUS[name]
    spec = error_by_name(name)
    payload = Problem(
        type=f"https://aconsole.local/errors/{name.lower().replace('_', '-')}",
        title=_TITLES[name],
        status=status,
        detail=detail,
        instance=instance,
        reason=name,
        retryable=spec.retryable,
    )
    return HttpResponse(status=status, body=_ENCODER.encode(payload), content_type=PROBLEM)


def json_response(body: bytes) -> HttpResponse:
    return HttpResponse(status=OK, body=body, content_type=JSON)


def no_content() -> HttpResponse:
    return HttpResponse(status=NO_CONTENT, body=b"", content_type=None)
