"""Read a Codex rollout from the byte we have not seen yet.

Plain ``.jsonl`` is seeked. ``.jsonl.zst`` is inflated with ``compression.zstd`` once per
unchanged file. A last line with no newline stays unread until it is finished.
"""

from __future__ import annotations

import compression.zstd
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import msgspec

from aconsole.core.domain.quota import QuotaKey

_SUFFIXES: Final = (".jsonl", ".jsonl.zst")
_EVENT: Final = "token_count"
_MINUTES_5H: Final = 300
_MINUTES_7D: Final = 10080
_WINDOWS: Final = {_MINUTES_5H: QuotaKey.FIVE_HOUR, _MINUTES_7D: QuotaKey.SEVEN_DAY}


@dataclass(frozen=True, slots=True, kw_only=True)
class RolloutQuota:
    """One Codex rate-limit window taken from a ``token_count`` line."""

    key: QuotaKey
    used_pct: float
    resets_at: int


@dataclass(frozen=True, slots=True, kw_only=True)
class RolloutChunk:
    """Complete lines and the offset to pass next time. ``path`` is None when no file matches."""

    path: Path | None
    lines: tuple[bytes, ...]
    offset: int


class RolloutTail:
    """Follow one session file. A newer file, or a shorter file, starts again at byte 0."""

    _root: Path
    _path: Path | None
    _offset: int
    _zst_stamp: tuple[int, int] | None
    _zst_body: bytes | None

    def __init__(self, root: Path) -> None:
        self._root = root
        self._path = None
        self._offset = 0
        self._zst_stamp = None
        self._zst_body = None

    def read(self, session_id: str) -> RolloutChunk:
        """New complete lines for ``session_id``. Incomplete trailing bytes are kept for later."""
        path = find_rollout(self._root, session_id)
        if path is None:
            self._path = None
            self._offset = 0
            self._zst_stamp = None
            self._zst_body = None
            return RolloutChunk(path=None, lines=(), offset=0)
        if path != self._path:
            self._path = path
            self._offset = 0
            self._zst_stamp = None
            self._zst_body = None
        lines, offset = self._pull(path)
        self._offset = offset
        return RolloutChunk(path=path, lines=lines, offset=offset)

    def _pull(self, path: Path) -> tuple[tuple[bytes, ...], int]:
        if path.name.endswith(".zst"):
            raw = self._inflated(path)
            offset = self._offset if self._offset <= len(raw) else 0
            lines, consumed = _split(raw[offset:])
            return lines, offset + consumed
        size = path.stat().st_size
        offset = self._offset if self._offset <= size else 0
        with path.open("rb") as handle:
            _ = handle.seek(offset)
            chunk = handle.read()
        lines, consumed = _split(chunk)
        return lines, offset + consumed

    def _inflated(self, path: Path) -> bytes:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        body = self._zst_body
        if body is not None and self._zst_stamp == stamp:
            return body
        raw = compression.zstd.decompress(path.read_bytes())
        self._zst_stamp = stamp
        self._zst_body = raw
        return raw


def find_rollout(root: Path, session_id: str) -> Path | None:
    """The newest ``rollout-*-<session_id>.jsonl`` or ``.jsonl.zst`` under ``root``."""
    if not _safe_id(session_id) or root.is_symlink() or not root.is_dir():
        return None
    found: list[Path] = []
    for path in root.rglob("rollout-*"):
        if path.is_symlink() or not path.is_file():
            continue
        if not _named(path, session_id) or not _inside(root, path):
            continue
        found.append(path)
    if not found:
        return None
    found.sort(key=_rank)
    return found[-1]


def quotas(line: bytes) -> tuple[RolloutQuota, ...]:
    """``token_count`` windows. Any other line, including a broken one, is empty."""
    windows = _limits(line)
    if windows is None:
        return ()
    found: list[RolloutQuota] = []
    for name in ("primary", "secondary"):
        window = _window(windows.get(name))
        if window is not None:
            found.append(window)
    return tuple(found)


def _split(chunk: bytes) -> tuple[tuple[bytes, ...], int]:
    if b"\n" not in chunk:
        return (), 0
    parts = chunk.split(b"\n")
    if chunk.endswith(b"\n"):
        complete = parts[:-1]
        consumed = len(chunk)
    else:
        complete = parts[:-1]
        consumed = sum(len(line) + 1 for line in complete)
    return tuple(line for line in complete if line != b""), consumed


def _window(raw: msgspec.Raw | None) -> RolloutQuota | None:
    if raw is None:
        return None
    try:
        obj = msgspec.json.decode(raw, type=dict[str, msgspec.Raw])
    except msgspec.DecodeError:
        return None
    minutes = _int(obj.get("window_minutes"))
    used = _number(obj.get("used_percent"))
    if used is None:
        used = _number(obj.get("used_percentage"))
    resets = _int(obj.get("resets_at"))
    if minutes is None or used is None or resets is None:
        return None
    key = _WINDOWS.get(minutes)
    if key is None:
        return None
    return RolloutQuota(key=key, used_pct=used, resets_at=resets)


def _limits(line: bytes) -> dict[str, msgspec.Raw] | None:
    body = _payload(line)
    if body is None or _text(body.get("type")) != _EVENT:
        return None
    limits = body.get("rate_limits")
    if limits is None:
        return None
    try:
        return msgspec.json.decode(limits, type=dict[str, msgspec.Raw])
    except msgspec.DecodeError:
        return None


def _payload(line: bytes) -> dict[str, msgspec.Raw] | None:
    try:
        obj = msgspec.json.decode(line, type=dict[str, msgspec.Raw])
    except msgspec.DecodeError:
        return None
    if _text(obj.get("type")) != "event_msg":
        return None
    payload = obj.get("payload")
    if payload is None:
        return None
    try:
        return msgspec.json.decode(payload, type=dict[str, msgspec.Raw])
    except msgspec.DecodeError:
        return None


def _named(path: Path, session_id: str) -> bool:
    name = path.name
    return any(name.endswith(suffix) and name[: -len(suffix)].endswith(f"-{session_id}") for suffix in _SUFFIXES)


def _safe_id(session_id: str) -> bool:
    if session_id == "" or "/" in session_id or "\\" in session_id:
        return False
    return all(char.isalnum() or char in "-_" for char in session_id)


def _inside(root: Path, path: Path) -> bool:
    try:
        _ = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _rank(path: Path) -> tuple[int, int, str]:
    stat = path.stat()
    plain = 1 if path.suffix == ".jsonl" else 0
    return (stat.st_mtime_ns, plain, str(path))


def _text(raw: msgspec.Raw | None) -> str | None:
    if raw is None:
        return None
    try:
        return msgspec.json.decode(raw, type=str)
    except msgspec.DecodeError:
        return None


def _int(raw: msgspec.Raw | None) -> int | None:
    if raw is None:
        return None
    try:
        return msgspec.json.decode(raw, type=int)
    except msgspec.DecodeError:
        return None


def _number(raw: msgspec.Raw | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(msgspec.json.decode(raw, type=int))
    except msgspec.DecodeError:
        pass
    try:
        return msgspec.json.decode(raw, type=float)
    except msgspec.DecodeError:
        return None
