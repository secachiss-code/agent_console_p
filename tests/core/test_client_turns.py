"""A slow request does not hold up a decision on the same connection, and ordinary requests keep their order."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import msgspec

from aconsole.contracts.app import EmptyParams
from aconsole.contracts.ipc import Request, ResponseErr, ResponseOk
from aconsole.core.bootstrap import _MAX_APART
from aconsole.core.infrastructure.ipc.framing import decode_envelope, encode_line
from tests.core.test_wire import _connect, _daemon, _stop

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from tests.core.test_wire import _Client

_ENCODER = msgspec.json.Encoder()
_SLOW_S = 1.0
_PROMPT_S = 0.4


def _slow(service: object, monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    original = service.dispatch  # pyright: ignore[reportAttributeAccessIssue] -- every service has it

    def dispatch(name: str, params: bytes) -> msgspec.Struct:
        if name == method:
            time.sleep(_SLOW_S)
        return original(name, params)

    monkeypatch.setattr(service, "dispatch", dispatch)


async def _send(client: _Client, request_id: int, method: str) -> None:
    request = Request(id=request_id, method=method, params=msgspec.Raw(_ENCODER.encode(EmptyParams())))
    client.writer.write(encode_line(_ENCODER, request))
    await client.writer.drain()


async def _reply(client: _Client) -> ResponseOk | ResponseErr:
    while True:
        envelope = decode_envelope(await asyncio.wait_for(client.reader.readline(), 5))
        if isinstance(envelope, (ResponseOk, ResponseErr)):
            return envelope


def _scenario(tmp_path: Path, method: str, attr: str, monkeypatch: pytest.MonkeyPatch) -> float:
    async def run() -> float:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        _slow(getattr(daemon, attr), monkeypatch, method)
        await _send(client, 100, method)
        await asyncio.sleep(0.05)
        started = time.monotonic()
        await _send(client, 101, "approval/decide")
        first = await _reply(client)
        elapsed = time.monotonic() - started
        assert first.id == 101
        assert (await _reply(client)).id == 100
        await _stop(daemon, task, client)
        return elapsed

    return asyncio.run(run())


def test_a_decision_is_answered_during_a_long_impact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _scenario(tmp_path, "knowledge/impact", "knowledge", monkeypatch) < _PROMPT_S


def test_a_decision_is_answered_during_a_long_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _scenario(tmp_path, "checkpoint/list", "review", monkeypatch) < _PROMPT_S


def test_ordinary_requests_keep_their_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        _slow(daemon.review, monkeypatch, "checkpoint/list")
        await _send(client, 100, "checkpoint/list")
        await _send(client, 101, "app/snapshot")
        assert [(await _reply(client)).id, (await _reply(client)).id] == [100, 101]
        await _stop(daemon, task, client)

    asyncio.run(run())


def test_more_impacts_than_the_daemon_runs_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        _slow(daemon.knowledge, monkeypatch, "knowledge/impact")
        for offset in range(_MAX_APART + 1):
            await _send(client, 100 + offset, "knowledge/impact")
        refused = await _reply(client)
        assert isinstance(refused, ResponseErr)
        assert (refused.id, refused.error.message) == (100 + _MAX_APART, "SERVER_BUSY")
        for _ in range(_MAX_APART):
            _ = await _reply(client)
        await _stop(daemon, task, client)

    asyncio.run(run())
