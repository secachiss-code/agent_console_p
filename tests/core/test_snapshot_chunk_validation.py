"""Malformed transfers never publish a partial or unbounded snapshot."""

from __future__ import annotations

import base64

import pytest

from aconsole.contracts.app import SnapshotChunkParams, SnapshotChunkResult, SnapshotResult
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
from aconsole.core.infrastructure.ipc import snapshot_chunks as server_chunks
from aconsole.core.infrastructure.ipc.snapshot_chunks import SnapshotChunks
from aconsole.ui.client.snapshot_chunks import SnapshotAssembly


def _chunk(*, data: bytes = b"{", cursor: str | None = "a", seq: int = 1, total: int = 4) -> SnapshotChunkResult:
    return SnapshotChunkResult(data=base64.b64encode(data).decode(), next_cursor=cursor, seq=seq, total_bytes=total)


@pytest.mark.parametrize("bad", ["base64", "sequence", "total", "cursor", "empty", "budget", "premature_end"])
def test_invalid_snapshot_chunk_is_rejected(bad: str) -> None:
    assembly = SnapshotAssembly()
    assert not assembly.add(_chunk())
    if bad == "base64":
        chunk = SnapshotChunkResult(data="!", next_cursor="b", seq=1, total_bytes=4)
    elif bad == "sequence":
        chunk = _chunk(seq=2, cursor="b")
    elif bad == "total":
        chunk = _chunk(total=5, cursor="b")
    elif bad == "cursor":
        chunk = _chunk()
    elif bad == "empty":
        chunk = _chunk(data=b"", cursor="b")
    elif bad == "budget":
        chunk = _chunk(total=64 * 1024 * 1024 + 1, cursor="b")
    else:
        chunk = _chunk(cursor=None)
    def accept() -> None:
        if assembly.add(chunk):
            _ = assembly.finish()

    with pytest.raises(ConnectionError):
        accept()


def test_expired_snapshot_cursor_frees_the_frozen_payload() -> None:
    chunks = SnapshotChunks(4096)
    first = chunks.read(SnapshotChunkParams(max_bytes=32), SnapshotResult(snapshot=demo_snapshot(), seq=7))
    assert first.next_cursor is not None
    chunks._until = 0.0
    with pytest.raises(DomainError) as caught:
        _ = chunks.read(SnapshotChunkParams(cursor=first.next_cursor))
    assert caught.value.name == "RESYNC_REQUIRED"
    assert chunks._data is None


def test_snapshot_budget_refuses_oversized_state_before_transfer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_chunks, "MAX_SNAPSHOT_BYTES", 1)
    chunks = SnapshotChunks(4096)
    with pytest.raises(DomainError) as caught:
        _ = chunks.read(SnapshotChunkParams(), SnapshotResult(snapshot=demo_snapshot(), seq=7))
    assert caught.value.name == "MESSAGE_TOO_LARGE"
    assert chunks._data is None
