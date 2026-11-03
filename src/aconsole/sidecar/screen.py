"""What an agent without hooks shows: when input was submitted and when the screen went quiet.

A working TUI keeps animating, so silence means it stopped to wait. This only reports; the core decides what the
text means. It runs only for agents the launch spec asks it for.
"""

from __future__ import annotations

import re
from typing import Final, final

QUIET_S: Final = 1.0
SUBMITTED: Final = "input_submitted"
QUIET: Final = "screen_quiet"
ACTIVE: Final = "screen_active"
_HOLD: Final = 16 * 1024
_LINES: Final = 12
_TEXT: Final = 2000
_CSI: Final = rb"\x1b\[[0-?]*[ -/]*[@-~]"
_OSC: Final = rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"
_SHORT: Final = rb"\x1b[ -/]*[0-~]"
_CONTROL: Final = rb"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
_ESCAPES: Final = re.compile(b"|".join((_CSI, _OSC, _SHORT, _CONTROL)))


@final
class NoWatch:
    """For agents that report their own status."""

    def output(self, _data: bytes, _now: float) -> tuple[str, str | None] | None:
        return None

    def input(self, _data: bytes) -> tuple[str, str | None] | None:
        return None

    def tick(self, _now: float) -> tuple[str, str | None] | None:
        return None


@final
class ScreenWatch:
    """Fed from the pump. Each method returns the signal to send, as (kind, text), or None."""

    def __init__(self) -> None:
        self._tail = bytearray()
        self._last_output: float | None = None
        self._quiet = False

    def output(self, data: bytes, now: float) -> tuple[str, str | None] | None:
        self._tail += data
        del self._tail[:-_HOLD]
        self._last_output = now
        was_quiet = self._quiet
        self._quiet = False
        return (ACTIVE, None) if was_quiet else None

    def input(self, data: bytes) -> tuple[str, str | None] | None:
        """A question already answered must not be found on the screen again."""
        self._tail.clear()
        return (SUBMITTED, None) if b"\r" in data or b"\n" in data else None

    def tick(self, now: float) -> tuple[str, str | None] | None:
        last = self._last_output
        if self._quiet or last is None or now - last < QUIET_S:
            return None
        self._quiet = True
        return QUIET, visible_tail(bytes(self._tail))


def visible_tail(raw: bytes) -> str:
    """The last lines that hold text, with terminal controls removed."""
    plain = _ESCAPES.sub(b"", raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).decode(errors="replace")
    lines = [line.rstrip() for line in plain.split("\n") if line.strip()]
    return "\n".join(lines[-_LINES:])[-_TEXT:]
