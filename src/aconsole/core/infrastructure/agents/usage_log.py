"""Tokens per model, counted from the session logs Claude and Codex write in their containers.

Neither CLI reports this over a question, but each logs every reply. Only the numbers and the model's name are read;
what was said is not kept. A file is read again only when it changed.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, final

import msgspec

from aconsole.contracts.app import ModelUsageView

_LOG = logging.getLogger(__name__)
_UNKNOWN_MODEL: Final = "unknown"
_SYNTHETIC_MODEL: Final = "<synthetic>"
_MODEL_CHARS: Final = 64


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """One reply. `key` names a reply logged more than once; the last line of that reply counts."""

    key: str | None
    at: datetime
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


_CHUNK_BYTES: Final = 64 * 1024
_MAX_LINE_BYTES: Final = 2 * 1024 * 1024


class _TokenInfo(msgspec.Struct):
    total_token_usage: dict[str, object] | None = None
    last_token_usage: dict[str, object] | None = None


class _CodexPayload(msgspec.Struct):
    type: str = ""
    model: str | None = None
    info: _TokenInfo | None = None


class _CodexLine(msgspec.Struct):
    type: str = ""
    timestamp: str | None = None
    payload: _CodexPayload | None = None


def _codex_line(line: bytes) -> _CodexLine | None:
    if b'"turn_context"' not in line and b'"token_count"' not in line:
        return None
    try:
        return msgspec.json.decode(line, type=_CodexLine)
    except msgspec.DecodeError:
        return None


@final
class _UsageParser:
    def __init__(self, *, codex: bool) -> None:
        self.codex = codex
        self.model = _UNKNOWN_MODEL
        self.total: object = None

    def feed(self, line: bytes) -> UsageEvent | None:
        if not self.codex:
            return _claude_event(_line(line)) if b'"usage"' in line and b'"assistant"' in line else None
        return self._codex(line)

    def _codex(self, line: bytes) -> UsageEvent | None:
        document = _codex_line(line)
        if document is None:
            return None
        payload = document.payload
        if payload is None:
            return None
        if document.type == "turn_context":
            if payload.model:
                self.model = payload.model[:_MODEL_CHARS]
            return None
        info = payload.info
        if payload.type != "token_count" or info is None or info.last_token_usage is None:
            return None
        at = _moment(document.timestamp)
        if at is None or info.total_token_usage == self.total:
            return None
        self.total = info.total_token_usage
        return _codex_event(at, self.model, info.last_token_usage)

    def preview(self, line: bytes) -> UsageEvent | None:
        model, total = self.model, self.total
        try:
            return self.feed(line)
        finally:
            self.model, self.total = model, total


@final
class _UsageCursor:
    def __init__(self, *, codex: bool) -> None:
        self.parser = _UsageParser(codex=codex)
        self.stamp: tuple[int, int, int, int] | None = None
        self.offset = 0
        self.tail = b""
        self.discarding = False
        self.events: list[UsageEvent] = []

    def append(self, data: bytes) -> None:
        parts = (self.tail + data).split(b"\n")
        self.tail = parts.pop()
        if self.discarding:
            if not parts:
                self.tail = b""
                return
            parts = parts[1:]
            self.discarding = False
        for line in parts:
            if len(line) <= _MAX_LINE_BYTES:
                event = self.parser.feed(line)
                if event is not None:
                    self.events.append(event)
        if len(self.tail) > _MAX_LINE_BYTES:
            self.tail = b""
            self.discarding = True
            _LOG.warning("oversized session log line skipped")


@final
class UsageLog:
    """Tail known CLI logs; retain model/dedup state and an incomplete trailing line."""

    def __init__(self, folder: Callable[[], Path | None], read: Callable[[Path], Iterable[UsageEvent]]) -> None:
        self._folder = folder
        self._read = read
        self._files: dict[Path, _UsageCursor] = {}
        self._since: datetime | None = None

    def models(self, since: datetime) -> tuple[ModelUsageView, ...]:
        folder = self._folder()
        if folder is None:
            self._files.clear()
            return ()
        if self._since is not None and since < self._since:
            # A wider requested window legitimately requires reading older history again.
            self._files.clear()
        self._since = since
        files: dict[Path, _UsageCursor] = {}
        events: list[UsageEvent] = []
        for path in folder.rglob("*.jsonl"):
            cursor = self._current(path, since)
            if cursor is not None:
                files[path] = cursor
                events.extend(cursor.events)
                if cursor.tail and not cursor.discarding:
                    provisional = cursor.parser.preview(cursor.tail)
                    if provisional is not None and provisional.at >= since:
                        events.append(provisional)
        self._files = files
        return _totals(events)

    def _current(self, path: Path, since: datetime) -> _UsageCursor | None:
        try:
            status = path.stat()
            if status.st_mtime < since.timestamp():
                return None
            stamp = (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns)
            cursor = self._files.get(path)
            if cursor is not None and cursor.stamp == stamp:
                cursor.events = [event for event in cursor.events if event.at >= since]
                return cursor
            if (cursor is None or cursor.stamp is None or cursor.stamp[:2] != stamp[:2]
                    or status.st_size <= cursor.offset):
                cursor = _UsageCursor(codex=self._read is codex_events)
            if self._read not in {claude_events, codex_events}:
                # Preserve the callback contract for custom readers with their own formats.
                cursor.events = [event for event in self._read(path) if event.at >= since]
            else:
                self._append_file(path, cursor, status)
                cursor.events = [event for event in cursor.events if event.at >= since]
            cursor.stamp = stamp
        except OSError:
            _LOG.warning("cannot read the session log %s", path.name, exc_info=True)
            return None
        return cursor

    @staticmethod
    def _append_file(path: Path, cursor: _UsageCursor, status: os.stat_result) -> None:
        with path.open("rb") as stream:
            _ = stream.seek(cursor.offset)
            left = status.st_size - cursor.offset
            while left > 0:
                data = stream.read(min(left, _CHUNK_BYTES))
                if not data:
                    break
                cursor.offset += len(data)
                left -= len(data)
                cursor.append(data)


def _totals(events: Iterable[UsageEvent]) -> tuple[ModelUsageView, ...]:
    unique: dict[object, UsageEvent] = {}
    for index, event in enumerate(events):
        unique[event.key if event.key is not None else index] = event
    totals: dict[str, list[int]] = {}
    for event in unique.values():
        row = totals.setdefault(event.model, [0, 0, 0, 0])
        row[0] += event.input_tokens
        row[1] += event.output_tokens
        row[2] += event.cache_read_tokens
        row[3] += event.cache_write_tokens
    return tuple(
        ModelUsageView(
            model=model, input_tokens=row[0], output_tokens=row[1], cache_read_tokens=row[2], cache_write_tokens=row[3],
        )
        for model, row in sorted(totals.items(), key=lambda item: -sum(item[1]))
    )


def claude_events(path: Path) -> Iterable[UsageEvent]:
    """Each assistant line of a Claude session carries its model and usage. One reply can take several lines."""
    with path.open("rb") as lines:
        for line in lines:
            if b'"usage"' not in line or b'"assistant"' not in line:
                continue
            event = _claude_event(_line(line))
            if event is not None:
                yield event


def _claude_event(document: dict[str, object] | None) -> UsageEvent | None:
    if document is None or document.get("type") != "assistant":
        return None
    message = document.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    model = message.get("model")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    at = _moment(document.get("timestamp"))
    if not isinstance(usage, dict) or not isinstance(model, str) or model == _SYNTHETIC_MODEL or at is None:
        return None
    return UsageEvent(
        key=f"{message.get('id')}:{document.get('requestId')}",  # pyright: ignore[reportUnknownMemberType]
        at=at,
        model=model[:_MODEL_CHARS],
        input_tokens=_count(usage.get("input_tokens")),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        output_tokens=_count(usage.get("output_tokens")),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        cache_read_tokens=_count(usage.get("cache_read_input_tokens")),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        cache_write_tokens=_count(usage.get("cache_creation_input_tokens")),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    )


def codex_events(path: Path) -> Iterable[UsageEvent]:
    """The same stateful decoder used by incremental reads, exposed for standalone readers."""
    parser = _UsageParser(codex=True)
    with path.open("rb") as lines:
        for line in lines:
            event = parser.feed(line)
            if event is not None:
                yield event


def _codex_event(at: datetime, model: str, last: dict[str, object]) -> UsageEvent:
    cached = _count(last.get("cached_input_tokens"))
    return UsageEvent(
        key=None,
        at=at,
        model=model,
        input_tokens=max(0, _count(last.get("input_tokens")) - cached),
        output_tokens=_count(last.get("output_tokens")),
        cache_read_tokens=cached,
        cache_write_tokens=_count(last.get("cache_write_input_tokens")),
    )


def _line(line: bytes) -> dict[str, object] | None:
    try:
        document: object = json.loads(line)  # pyright: ignore[reportAny] -- checked just below
    except ValueError:
        return None
    return document if isinstance(document, dict) else None  # pyright: ignore[reportUnknownVariableType]


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0
