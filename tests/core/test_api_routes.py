"""The published method table must reach handlers in the assembled daemon."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from tools.check_contracts import check_method_routes, probe_method_routes

from aconsole.contracts.methods import METHODS
from aconsole.core.infrastructure.ipc import session
from tests.core.test_wire import _connect, _daemon, _stop

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_published_methods_reach_the_real_core(tmp_path: Path) -> None:
    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        serving = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            assert await probe_method_routes(daemon.socket, tuple(method.name for method in METHODS)) == []
        finally:
            await _stop(daemon, serving, client)

    asyncio.run(scenario())


def test_a_missing_handler_fails_even_when_the_client_method_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session, "PALETTE_METHODS", session.PALETTE_METHODS - {"palette/items"})

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        serving = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            assert await probe_method_routes(daemon.socket, ("palette/items",)) == [
                "core route palette/items: METHOD_NOT_FOUND",
            ]
        finally:
            await _stop(daemon, serving, client)

    asyncio.run(scenario())


def test_contract_check_runs_its_own_isolated_daemon() -> None:
    from pathlib import Path

    assert check_method_routes(Path(__file__).resolve().parents[2]) == []
