"""A frozen UTF-8 snapshot can cross a smaller NDJSON transport in bounded chunks."""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING

import msgspec
import pytest

from aconsole.contracts.app import AgentInfo, AppSnapshot
from aconsole.contracts.common import AgentKind
from aconsole.contracts.events import AgentsChanged
from aconsole.contracts.ipc import ResponseErr, ResponseOk
from aconsole.core.application.services.tabs import TabService
from aconsole.ui.client.core_client import CoreClient
from tests.core.test_wire import _connect, _daemon, _stop

if TYPE_CHECKING:
    from pathlib import Path

_LABEL = "данные🧪" * 6000


class _Query(msgspec.Struct, frozen=True, kw_only=True):
    cursor: str | None = None
    max_bytes: int = 1024


class _Chunk(msgspec.Struct, frozen=True, kw_only=True):
    data: str
    next_cursor: str | None
    seq: int
    total_bytes: int


def _inflate(monkeypatch: pytest.MonkeyPatch) -> list[AgentInfo]:
    original = TabService.snapshot
    agent = AgentInfo(
        kind=AgentKind.CLAUDE,
        display_name=_LABEL,
        container="unused",
        available=False,
        unavailable_reason="test",
        logged_in=None,
        version=None,
        supported=True,
        capabilities=frozenset(),
    )

    state = [agent]

    def big(self: TabService) -> AppSnapshot:
        return msgspec.structs.replace(original(self), agents=tuple(state))

    monkeypatch.setattr(TabService, "snapshot", big)
    return state


def test_client_loads_a_large_utf8_snapshot_without_reconnecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _inflate(monkeypatch)

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        daemon._frame_limit = 16 * 1024
        serving = asyncio.create_task(daemon.serve())
        ipc = await _connect(daemon.socket)
        client = CoreClient(str(daemon.socket))
        try:
            _ = await client.connect()
            snapshot, _ = await client.pull_snapshot()
            assert snapshot.snapshot.agents[0].display_name == _LABEL
            assert client.store.snapshot is not None
            assert client.store.snapshot.agents[0].display_name == _LABEL
            assert client.connects == 1
            assert not client.store.reconnecting
        finally:
            await client.close()
            await _stop(daemon, serving, ipc)

    asyncio.run(scenario())


def test_chunks_are_frozen_bounded_and_reject_stale_cursors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = _inflate(monkeypatch)

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        daemon._frame_limit = 16 * 1024
        serving = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            reply = await client.raw("app/snapshotChunk", _Query())
            assert isinstance(reply, ResponseOk), reply
            first = msgspec.json.decode(reply.result, type=_Chunk)
            assert first.next_cursor is not None
            state[0] = msgspec.structs.replace(state[0], display_name="changed after capture")
            daemon._approval_event(AgentsChanged(agents=tuple(state)))
            assert daemon._ring.current_seq > first.seq
            data = bytearray(base64.b64decode(first.data, validate=True))
            cursor = first.next_cursor
            while cursor is not None:
                reply = await client.raw("app/snapshotChunk", _Query(cursor=cursor))
                assert isinstance(reply, ResponseOk), reply
                chunk = msgspec.json.decode(reply.result, type=_Chunk)
                assert chunk.seq == first.seq
                assert chunk.total_bytes == first.total_bytes
                raw = base64.b64decode(chunk.data, validate=True)
                assert 0 < len(raw) <= 1024
                data.extend(raw)
                assert len(data) <= first.total_bytes
                assert chunk.next_cursor != cursor
                cursor = chunk.next_cursor
            assert len(data) == first.total_bytes
            snapshot = msgspec.json.decode(data, type=AppSnapshot)
            assert snapshot.agents[0].display_name == _LABEL
            refused = await client.raw("app/snapshotChunk", _Query(cursor=first.next_cursor))
            assert isinstance(refused, ResponseErr)
            assert refused.error.message == "RESYNC_REQUIRED"
        finally:
            await _stop(daemon, serving, client)

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", [0, -1, 1048577])
def test_invalid_chunk_limits_do_not_close_the_connection(tmp_path: Path, limit: int) -> None:
    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        serving = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            answer = await client.raw("app/snapshotChunk", _Query(max_bytes=limit))
            assert isinstance(answer, ResponseErr)
            assert answer.error.message == "INVALID_PARAMS"
            assert (await client.snapshot()).api_version
        finally:
            await _stop(daemon, serving, client)

    asyncio.run(scenario())
