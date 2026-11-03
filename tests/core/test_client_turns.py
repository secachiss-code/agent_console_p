"""A slow request does not hold up a decision on the same connection, and ordinary requests keep their order."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import msgspec
from sqlalchemy.exc import OperationalError

from aconsole.contracts.app import EmptyParams, HealthResult
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
_STATEMENT = "INSERT"


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


def test_git_requests_past_the_slots_wait_and_are_not_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        _slow(daemon.git, monkeypatch, "git/state")
        waiting = _MAX_APART + 2
        for offset in range(waiting):
            await _send(client, 100 + offset, "git/state")
        answers = [await _reply(client) for _ in range(waiting)]
        assert sorted(item.id for item in answers) == [100 + offset for offset in range(waiting)]
        assert not [item for item in answers if isinstance(item, ResponseErr) and item.error.message == "SERVER_BUSY"]
        await _stop(daemon, task, client)

    asyncio.run(run())


def test_health_is_answered_while_the_worker_is_busy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        _slow(daemon.review, monkeypatch, "checkpoint/list")
        await _send(client, 100, "checkpoint/list")
        await asyncio.sleep(0.05)
        started = time.monotonic()
        await _send(client, 101, "app/health")
        reply = await _reply(client)
        elapsed = time.monotonic() - started
        assert isinstance(reply, ResponseOk)
        assert reply.id == 101
        assert elapsed < _PROMPT_S
        health = msgspec.json.decode(reply.result, type=HealthResult)
        assert health.database is (daemon._writer is not None)
        assert health.writer_failed == 0
        _ = await _reply(client)
        await _stop(daemon, task, client)

    asyncio.run(run())


def test_health_counts_failed_writes_and_slow_answers(tmp_path: Path) -> None:
    async def run() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        await _send(client, 100, "app/snapshot")
        _ = await _reply(client)
        writer = daemon._writer
        assert writer is not None

        def broken(_session: object) -> None:
            raise OperationalError(_STATEMENT, {}, ValueError())

        writer.post(broken)
        _ = writer.call(lambda _session: None)
        health = await client.call("app/health", EmptyParams(), HealthResult)
        assert health.writer_failed == 1
        assert health.ipc_answered >= 1
        assert health.ipc_slowest_ms > 0
        await _stop(daemon, task, client)

    asyncio.run(run())
