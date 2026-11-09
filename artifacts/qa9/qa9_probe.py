"""Optional pytest probe: record real rewrite RPC replies without changing them."""

# ruff: noqa: INP001 -- loaded explicitly by pytest from the artifact directory

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec

from aconsole.ui.client.core_client import CoreClient

if TYPE_CHECKING:
    import pytest

_request = CoreClient.request


def _record(message: str) -> None:
    trace = Path(os.environ["ACONSOLE_QA9_TRACE"])
    with trace.open("a", encoding="utf-8") as stream:
        _ = stream.write(f"{time.monotonic():.6f} {message}\n")


async def _observed[T: msgspec.Struct](
    self: CoreClient, method: str, params: msgspec.Struct, result_type: type[T],
) -> T:
    if method not in {"git/rebase", "git/abort"}:
        return await _request(self, method, params, result_type)
    _record(f"request {method} {params!r}")
    try:
        result = await _request(self, method, params, result_type)
    except Exception as error:
        _record(f"error {method} {error!r}")
        raise
    _record(f"result {method} {result!r}")
    return result


def pytest_configure(config: pytest.Config) -> None:
    CoreClient.request = _observed
    config.add_cleanup(lambda: setattr(CoreClient, "request", _request))
