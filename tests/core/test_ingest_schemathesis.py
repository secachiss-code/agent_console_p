"""Schemathesis checks the ingest OpenAPI document against the in-process ASGI app.

The product app does not publish its schema. This test wraps it with one GET
``/openapi.json`` that returns ``contracts/schema/ingest.openapi.yaml`` (the file
is JSON). Every other request is the real ingest app. No operation is excluded.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import schemathesis
from hypothesis import settings
from schemathesis.checks import not_a_server_error
from schemathesis.config import SchemathesisConfig
from schemathesis.generation.case import Case
from schemathesis.specs.openapi.checks import response_schema_conformance, status_code_conformance

from aconsole.core.infrastructure.http.app import IngestApp, Receive, Send
from aconsole.core.infrastructure.http.auth import TokenBook
from aconsole.core.infrastructure.http.routes import IngestQueue

if TYPE_CHECKING:
    from collections.abc import Mapping

_ROOT = Path(__file__).resolve().parents[2]
_DOCUMENT = (_ROOT / "contracts" / "schema" / "ingest.openapi.yaml").read_bytes()
_TAB = "tab-schemathesis"
_BOOK = TokenBook()
_TOKEN = _BOOK.issue(_TAB)
_CHECKS = (not_a_server_error, status_code_conformance, response_schema_conformance)
_CONFIG = SchemathesisConfig.from_dict(
    {
        "seed": 1,
        "headers": {"Authorization": f"Bearer {_TOKEN}"},
        "generation": {"max-examples": 8, "allow-x00": False},
    }
)


async def _send_json(send: Send, body: bytes) -> None:
    length = str(len(body)).encode("ascii")
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json"), (b"content-length", length)],
        }
    )
    await send({"type": "http.response.body", "body": body})


class _SchemaApp:
    """Serves the OpenAPI document, then the real ingest app. Stub: no product route was added."""

    def __init__(self, inner: IngestApp) -> None:
        self._inner = inner

    async def __call__(self, scope: Mapping[str, object], receive: Receive, send: Send) -> None:
        if scope.get("type") == "http" and scope.get("path") == "/openapi.json" and scope.get("method") == "GET":
            await _send_json(send, _DOCUMENT)
            return
        await self._inner(scope, receive, send)


_SCHEMA = schemathesis.openapi.from_asgi(
    "/openapi.json",
    _SchemaApp(IngestApp(_BOOK, IngestQueue())),
    config=_CONFIG,
)


@_SCHEMA.hook("map_path_parameters")
def _pin_tab(_context: object, path_parameters: dict[str, object] | None) -> dict[str, object] | None:
    """Keep the generated tab id on the fixture token. Other operations stay in the run."""
    if path_parameters is None or "tab_id" not in path_parameters:
        return path_parameters
    pinned = dict(path_parameters)
    pinned["tab_id"] = _TAB
    return pinned


@settings(max_examples=8, deadline=None)
@_SCHEMA.parametrize()
def test_ingest_schemathesis(case: Case) -> None:
    assert isinstance(case, Case)
    response = case.call_and_validate(checks=list(_CHECKS))
    assert response.status_code < 500
