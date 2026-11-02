"""When a Codex turn starts and ends, from its session files. Without trusted hooks nothing else says it starts."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final, final

import msgspec

if TYPE_CHECKING:
    from pathlib import Path

_TAIL_BYTES: Final = 262144
_STARTED: Final = frozenset({"task_started", "turn_started"})
_ENDED: Final = frozenset({"task_complete", "turn_complete", "turn_aborted"})


class _Payload(msgspec.Struct, frozen=True):
    type: str | None = None
    cwd: str | None = None


class _Line(msgspec.Struct, frozen=True):
    type: str | None = None
    payload: _Payload | None = None


def _decode(line: bytes) -> _Line | None:
    try:
        return msgspec.json.decode(line, type=_Line)
    except msgspec.DecodeError:
        return None


def _session_cwd(path: Path) -> str | None:
    with path.open("rb") as file:
        found = _decode(file.readline())
    if found is None or found.type != "session_meta" or found.payload is None:
        return None
    return found.payload.cwd


def _last_turn_state(path: Path) -> bool | None:
    """True while a turn runs, False after it ended, None when the tail shows neither."""
    with path.open("rb") as file:
        size = file.seek(0, 2)
        _ = file.seek(max(0, size - _TAIL_BYTES))
        lines = file.read().splitlines()
    for line in reversed(lines):
        if b'"event_msg"' not in line:
            continue
        found = _decode(line)
        kind = found.payload.type if found is not None and found.payload is not None else None
        if kind in _STARTED:
            return True
        if kind in _ENDED:
            return False
    return None


@final
class TurnWatch:
    """Reports each session whose turn state changed. Files of today and yesterday only; Codex names dirs by date."""

    _root: Path
    _stamps: dict[Path, int]
    _cwds: dict[Path, str | None]
    _states: dict[Path, bool]

    def __init__(self, root: Path) -> None:
        self._root = root
        self._stamps = {}
        self._cwds = {}
        self._states = {}

    def changes(self, now: datetime) -> tuple[tuple[str, bool], ...]:
        """`(cwd, working)` per changed session, oldest file first, so the newest session of a directory wins."""
        found: list[tuple[int, str, bool]] = []
        for day in (now - timedelta(days=1), now):
            folder = self._root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
            if folder.is_dir():
                found.extend(self._scan(folder))
        return tuple((cwd, working) for _stamp, cwd, working in sorted(found))

    def _scan(self, folder: Path) -> list[tuple[int, str, bool]]:
        found: list[tuple[int, str, bool]] = []
        for path in folder.glob("rollout-*.jsonl"):
            try:
                stamp = path.stat().st_mtime_ns
                if self._stamps.get(path) == stamp:
                    continue
                self._stamps[path] = stamp
                if path not in self._cwds:
                    self._cwds[path] = _session_cwd(path)
                state = _last_turn_state(path)
            except OSError:
                continue
            cwd = self._cwds[path]
            # New lines in a running turn are reported too: they end a wait for the user's approval.
            if cwd is None or state is None or (self._states.get(path) is state and not state):
                continue
            self._states[path] = state
            found.append((stamp, cwd, state))
        return found
