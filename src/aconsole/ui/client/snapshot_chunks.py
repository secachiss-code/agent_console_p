"""Validate bounded snapshot chunks before publishing a complete client state."""

import base64
import binascii

import msgspec

from aconsole.contracts.app import AppSnapshot, SnapshotChunkResult, SnapshotResult

MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
MAX_SNAPSHOT_CHUNKS = 65536
_MAX_CHUNK_BYTES = 1024 * 1024
_MAX_CURSOR_LENGTH = 128


class SnapshotAssembly:
    def __init__(self) -> None:
        self.cursor: str | None = None
        self._data: bytearray = bytearray()
        self._seq: int | None = None
        self._total: int | None = None
        self._seen: set[str] = set()

    def add(self, chunk: SnapshotChunkResult) -> bool:
        if not 0 < chunk.total_bytes <= MAX_SNAPSHOT_BYTES or chunk.seq < 0:
            message = "invalid snapshot byte budget or sequence"
            raise ConnectionError(message)
        if self._seq is None:
            self._seq, self._total = chunk.seq, chunk.total_bytes
        elif chunk.seq != self._seq or chunk.total_bytes != self._total:
            message = "snapshot changed during chunk transfer"
            raise ConnectionError(message)
        try:
            raw = base64.b64decode(chunk.data, validate=True)
        except (binascii.Error, ValueError) as exc:
            message = "invalid snapshot chunk encoding"
            raise ConnectionError(message) from exc
        if not raw or len(raw) > _MAX_CHUNK_BYTES or len(self._data) + len(raw) > chunk.total_bytes:
            message = "snapshot chunk exceeds its byte budget"
            raise ConnectionError(message)
        cursor = chunk.next_cursor
        if cursor is not None:
            if cursor in self._seen or len(cursor) > _MAX_CURSOR_LENGTH:
                message = "snapshot chunk cursor made no progress"
                raise ConnectionError(message)
            self._seen.add(cursor)
        self._data.extend(raw)
        self.cursor = cursor
        return cursor is None

    def finish(self) -> SnapshotResult:
        seq, total = self._seq, self._total
        if seq is None or total is None or self.cursor is not None or len(self._data) != total:
            message = "incomplete snapshot transfer"
            raise ConnectionError(message)
        return SnapshotResult(snapshot=msgspec.json.decode(self._data, type=AppSnapshot), seq=seq)
