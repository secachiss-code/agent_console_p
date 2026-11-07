"""Connection-local frozen snapshot chunks, bounded by bytes and lifetime."""

import base64
import secrets
import time

import msgspec

from aconsole.contracts.app import SnapshotChunkParams, SnapshotChunkResult, SnapshotResult
from aconsole.contracts.errors import error_by_name
from aconsole.core.domain.errors import DomainError

MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
MAX_CHUNK_BYTES = 1024 * 1024
_TTL_S = 30.0
_HEADROOM = 640
_OFFSET_DIGITS = 10
_TOO_LARGE = "MESSAGE_TOO_LARGE"
_RESYNC = "RESYNC_REQUIRED"


def _failure(name: str) -> DomainError:
    spec = error_by_name(name)
    return DomainError(code=spec.code, name=name, retryable=spec.retryable)


class SnapshotChunks:
    """One transfer per connection. Completing or expiring it releases the frozen bytes."""

    def __init__(self, frame_limit: int) -> None:
        self._frame_limit: int = frame_limit
        self._data: bytes | None = None
        self._key: str = ""
        self._seq: int = 0
        self._until: float = 0.0

    def read(self, query: SnapshotChunkParams, snapshot: SnapshotResult | None = None) -> SnapshotChunkResult:
        if not 1 <= query.max_bytes <= MAX_CHUNK_BYTES:
            message = "snapshot chunk size must be between 1 and 1048576 bytes"
            raise ValueError(message)
        if self._frame_limit <= _HEADROOM:
            raise _failure(_TOO_LARGE)
        if query.cursor is None:
            if snapshot is None:
                raise _failure(_RESYNC)
            encoded = msgspec.json.encode(snapshot.snapshot)
            if len(encoded) > MAX_SNAPSHOT_BYTES:
                self._data = None
                raise _failure(_TOO_LARGE)
            self._data, self._seq = encoded, snapshot.seq
            self._key = secrets.token_hex(16)
            self._until = time.monotonic() + _TTL_S
            offset = 0
        else:
            offset = self._offset(query.cursor)
        data = self._data
        if data is None:
            raise _failure(_RESYNC)
        size = min(query.max_bytes, (self._frame_limit - _HEADROOM) * 3 // 4)
        end = min(offset + size, len(data))
        cursor = f"{self._key}:{end}" if end < len(data) else None
        result = SnapshotChunkResult(
            data=base64.b64encode(data[offset:end]).decode("ascii"),
            next_cursor=cursor,
            seq=self._seq,
            total_bytes=len(data),
        )
        if cursor is None:
            self._data = None
            self._key = ""
        return result

    def _offset(self, cursor: str) -> int:
        if time.monotonic() >= self._until:
            self._data = None
        key, separator, number = cursor.partition(":")
        data = self._data
        if data is None or not separator or key != self._key or len(number) > _OFFSET_DIGITS or not number.isdecimal():
            raise _failure(_RESYNC)
        offset = int(number)
        if not 0 < offset < len(data):
            raise _failure(_RESYNC)
        return offset
