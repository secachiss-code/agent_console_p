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
_ESC: Final = 0x1B
_CR: Final = 0x0D
_LF: Final = 0x0A
_BS: Final = 0x08
_HT: Final = 0x09
_BEL: Final = 0x07
_DEL: Final = 0x7F
_SPACE: Final = 0x20
_CSI_LO: Final = 0x40
_CSI_HI: Final = 0x7E
_TAB_STOP: Final = 8


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
        self._echo = bytearray()
        self._last_output: float | None = None
        self._quiet = False

    def output(self, data: bytes, now: float) -> tuple[str, str | None] | None:
        echoed = self._take_echo(data)
        self._tail += data
        del self._tail[:-_HOLD]
        self._last_output = now
        was_quiet = self._quiet
        self._quiet = False
        # A keystroke the terminal printed back is not the agent starting work.
        if echoed:
            return None
        return (ACTIVE, None) if was_quiet else None

    def input(self, data: bytes) -> tuple[str, str | None] | None:
        """A submitted answer drops the question. Typing alone leaves it on the screen."""
        if b"\r" not in data and b"\n" not in data:
            self._echo += data
            return None
        self._tail.clear()
        self._echo.clear()
        return (SUBMITTED, None)

    def _take_echo(self, data: bytes) -> bool:
        """True when this chunk is only what the user just typed, plus cursor controls."""
        pending = bytes(self._echo)
        if pending == b"" or data == b"":
            return False
        if pending.startswith(data):
            del self._echo[: len(data)]
            return True
        if not data.startswith(pending):
            return False
        rest = data[len(pending) :]
        if rest != b"" and _ESCAPES.sub(b"", rest) != b"":
            return False
        self._echo.clear()
        return True

    def tick(self, now: float) -> tuple[str, str | None] | None:
        last = self._last_output
        if self._quiet or last is None or now - last < QUIET_S:
            return None
        self._quiet = True
        return QUIET, visible_tail(bytes(self._tail))


def visible_tail(raw: bytes) -> str:
    """The last lines a person would read. Erased and overwritten text does not stay glued on."""
    lines = [line.rstrip() for line in _paint(raw) if line.strip()]
    return "\n".join(lines[-_LINES:])[-_TEXT:]


def _paint(raw: bytes) -> list[str]:
    """Apply carriage returns, cursor motion and line erases. Other controls are dropped."""
    rows: list[list[str]] = [[]]
    column = 0
    index = 0
    while index < len(raw):
        byte = raw[index]
        if byte == _ESC:
            index, column = _escape(raw, index, rows, column)
            continue
        if byte == _CR:
            column = 0
        elif byte == _LF:
            rows.append([])
            column = 0
        elif byte == _BS:
            column = max(column - 1, 0)
        elif byte == _HT:
            column = column + _TAB_STOP - (column % _TAB_STOP)
        elif byte < _SPACE or byte == _DEL:
            pass
        else:
            char, index = _one_char(raw, index)
            row = rows[-1]
            if column < len(row):
                row[column] = char
            else:
                row.extend(" " * (column - len(row)))
                row.append(char)
            column += 1
            continue
        index += 1
    return ["".join(row) for row in rows]


def _escape(raw: bytes, index: int, rows: list[list[str]], column: int) -> tuple[int, int]:
    if index + 1 >= len(raw):
        return len(raw), column
    kind = raw[index + 1]
    if kind == ord("["):
        end = index + 2
        while end < len(raw) and not _CSI_LO <= raw[end] <= _CSI_HI:
            end += 1
        if end >= len(raw):
            return len(raw), column
        return end + 1, _csi(raw[index + 2 : end], raw[end], rows, column)
    if kind == ord("]"):
        end = index + 2
        while end < len(raw) and raw[end] not in {_BEL, _ESC}:
            end += 1
        if end < len(raw) and raw[end] == _ESC:
            end += 2
        else:
            end += 1
        return min(end, len(raw)), column
    return index + 2, column


def _csi(body: bytes, final: int, rows: list[list[str]], column: int) -> int:
    numbers = _numbers(body)
    if final == ord("C"):
        return column + (numbers[0] if numbers else 1)
    if final == ord("D"):
        return max(0, column - (numbers[0] if numbers else 1))
    if final == ord("G"):
        return max((numbers[0] if numbers else 1) - 1, 0)
    if final == ord("K"):
        mode = numbers[0] if numbers else 0
        row = rows[-1]
        if mode == 0:
            del row[column:]
        elif mode == 1:
            for at in range(min(column, len(row))):
                row[at] = " "
        else:
            row.clear()
            return 0
    return column


def _numbers(body: bytes) -> list[int]:
    text = bytes(byte for byte in body if byte in b"0123456789;")
    if text == b"":
        return []
    found: list[int] = []
    for part in text.split(b";"):
        if part == b"":
            continue
        found.append(int(part))
    return found


def _one_char(raw: bytes, index: int) -> tuple[str, int]:
    for size in (1, 2, 3, 4):
        chunk = raw[index : index + size]
        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if text != "":
            return text, index + size
    return "\ufffd", index + 1
