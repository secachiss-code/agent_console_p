"""Tokens per model, counted from the session logs Claude and Codex write in their containers.

Neither CLI reports this over a question, but each logs every reply. Only the numbers and the model's name are read;
what was said is not kept. A file is read again only when it changed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, final

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


@final
class UsageLog:
    """The `*.jsonl` files under one folder, added up by model since a moment."""

    def __init__(self, folder: Callable[[], Path | None], read: Callable[[Path], Iterable[UsageEvent]]) -> None:
        self._folder = folder
        self._read = read
        self._files: dict[Path, tuple[tuple[int, int], tuple[UsageEvent, ...]]] = {}

    def models(self, since: datetime) -> tuple[ModelUsageView, ...]:
        """Most used first. Empty when the folder cannot be found."""
        folder = self._folder()
        if folder is None:
            return ()
        files: dict[Path, tuple[tuple[int, int], tuple[UsageEvent, ...]]] = {}
        for path in folder.rglob("*.jsonl"):
            kept = self._current(path, since)
            if kept is not None:
                files[path] = kept
        self._files = files
        return _totals(event for _, events in files.values() for event in events if event.at >= since)

    def _current(self, path: Path, since: datetime) -> tuple[tuple[int, int], tuple[UsageEvent, ...]] | None:
        try:
            status = path.stat()
            if status.st_mtime < since.timestamp():
                return None
            stamp = (status.st_size, status.st_mtime_ns)
            cached = self._files.get(path)
            if cached is not None and cached[0] == stamp:
                return cached
            return stamp, tuple(self._read(path))
        except OSError:
            _LOG.warning("cannot read the session log %s", path.name, exc_info=True)
            return None


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
    """`turn_context` names the model; a `token_count` whose total moved adds its last reply. Input includes cached."""
    model = _UNKNOWN_MODEL
    total: object = None
    with path.open("rb") as lines:
        for line in lines:
            if b'"turn_context"' not in line and b'"token_count"' not in line:
                continue
            document = _line(line)
            payload = document.get("payload") if document is not None else None
            if document is None or not isinstance(payload, dict):
                continue
            if document.get("type") == "turn_context":
                named = payload.get("model")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                model = named[:_MODEL_CHARS] if isinstance(named, str) and named else model
                continue
            info = payload.get("info")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            if payload.get("type") != "token_count" or not isinstance(info, dict):  # pyright: ignore[reportUnknownMemberType]
                continue
            seen = info.get("total_token_usage")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            last = info.get("last_token_usage")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            at = _moment(document.get("timestamp"))
            if seen == total or not isinstance(last, dict) or at is None:
                continue
            total = seen  # pyright: ignore[reportUnknownVariableType]
            yield _codex_event(at, model, last)  # pyright: ignore[reportUnknownArgumentType]


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
