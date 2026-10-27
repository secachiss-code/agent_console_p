"""Hold terminal bytes between DEC 2026 BSU and ESU.

A nested BSU raises the depth. The held bytes leave on the matching ESU, on
the 50 ms timer, at 4 MiB, or at EOF. OSC 0/2/9/777 and a bare BEL are removed
from the stream and reported as signals; every other OSC (OSC 8 links, OSC 7,
OSC 133) passes through to the terminal. A marker split across reads stays
pending. An OSC without its terminator passes through raw after 250 ms or 4 KiB,
so it cannot hold the output back. Each read is scanned once from where the
previous scan stopped (linear in the bytes read).
"""

import re
from dataclasses import dataclass
from typing import Final, Literal, final

_BSU: Final = b"\x1b[?2026h"
_ESU: Final = b"\x1b[?2026l"
_OSC: Final = b"\x1b]"
# BEL, an OSC start, or a finished BSU/ESU. A marker cut off at the end is checked separately.
_MARK: Final = re.compile(rb"\x07|\x1b\]|\x1b\[\?2026[hl]")
_LEFT_BRACKET: Final = 0x5B
_RIGHT_BRACKET: Final = 0x5D
_ST: Final = b"\x1b\\"
_BEL: Final = 0x07
_ESC: Final = 0x1B
LIMIT: Final = 4 * 1024 * 1024
FLUSH_S: Final = 0.05
_OSC_HOLD: Final = 4096
_OSC_WAIT_S: Final = 0.25
# OSC numbers this framer turns into signals. One of them that is malformed is dropped, not passed on.
_OWN_OSC: Final = (b"\x1b]0;", b"\x1b]2;", b"\x1b]9;", b"\x1b]777;")
_TEXT: Final = 512
_ZERO: Final = 0
_ONE: Final = 1
_PROGRESS_MAX: Final = 100
# OSC 9;4 states (ConEmu / Windows Terminal): 0 remove, 1 value, 2 error, 3 indeterminate, 4 paused.
_PROGRESS_STATES: Final = {0: "remove", 1: "value", 2: "error", 3: "indeterminate", 4: "paused"}
_WITH_VALUE: Final = frozenset({1, 2, 4})

Kind = Literal["bell", "osc_title", "osc9_notify", "osc777_notify", "osc9_4_progress"]


@dataclass(frozen=True, kw_only=True)
class Signal:
    """One terminal signal pulled out of the byte stream."""

    kind: Kind
    text: str | None = None
    progress: int | None = None


@final
class Framer:
    """Stream scanner. `now` is injected so tests do not sleep."""

    def __init__(self, *, limit: int = LIMIT, flush_s: float = FLUSH_S) -> None:
        if limit <= _ZERO:
            message = "frame limit must be positive"
            raise ValueError(message)
        if flush_s <= _ZERO:
            message = "frame timeout must be positive"
            raise ValueError(message)
        self._limit = limit
        self._flush_s = flush_s
        self._pending = bytearray()
        self._held = bytearray()
        self._signals: list[Signal] = []
        self._depth = _ZERO
        self._held_since = 0.0
        self._osc_since: float | None = None
        self.bytes_out = _ZERO
        self.frames = _ZERO
        self.frames_timed_out = _ZERO
        self.max_frame_bytes = _ZERO

    def feed(self, data: bytes, now: float) -> bytes:
        """Accept one read. Return the bytes that can be shown now."""
        self._pending.extend(data)
        return self._drain(now)

    def flush_due(self, now: float) -> bytes:
        """Pass an overdue unterminated OSC through, and release a held frame when the timeout has elapsed."""
        out = bytearray()
        since = self._osc_since
        if since is not None and now - since >= _OSC_WAIT_S:
            self._give_up_osc(out)
            out.extend(self._drain(now))
        if self._depth <= _ZERO or not self._held:
            return bytes(out)
        if now - self._held_since < self._flush_s:
            return bytes(out)
        self._held_since = now
        out.extend(self._release(timed_out=True))
        return bytes(out)

    def finish(self) -> bytes:
        """EOF. Release a frame that never saw its ESU, and drop a partial marker."""
        out = bytearray(self._drain(self._held_since))
        self._pending.clear()
        self._osc_since = None
        if self._held:
            out.extend(self._release(timed_out=False))
        self._depth = _ZERO
        return bytes(out)

    def take_signals(self) -> tuple[Signal, ...]:
        """Return signals found since the previous call."""
        found = tuple(self._signals)
        self._signals.clear()
        return found

    def _drain(self, now: float) -> bytes:
        out = bytearray()
        data = self._pending
        pos = 0
        length = len(data)
        while pos < length:
            found = _next_special(data, pos)
            if found >= length:
                self._emit(data[pos:], out)
                pos = length
                break
            if found > pos:
                self._emit(data[pos:found], out)
            if data[found] == _BEL:
                self._signals.append(Signal(kind="bell"))
                pos = found + _ONE
                continue
            step = self._escape(data, found, out, now)
            if step is None:
                pos = found
                break
            pos = step
        del data[:pos]
        return bytes(out)

    def _escape(self, data: bytearray, pos: int, out: bytearray, now: float) -> int | None:
        """Consume the escape at `pos`. Returns the next position, or None to wait for more bytes."""
        nxt = pos + _ONE
        if nxt >= len(data):
            return None
        kind = data[nxt]
        if kind == _RIGHT_BRACKET:
            return self._osc(data, pos, out, now)
        if kind == _LEFT_BRACKET:
            if data.startswith(_BSU, pos):
                self._depth += _ONE
                if self._depth == _ONE:
                    self._held_since = now
                return pos + len(_BSU)
            if data.startswith(_ESU, pos):
                if self._depth > _ZERO:
                    self._depth -= _ONE
                    if self._depth == _ZERO:
                        out.extend(self._release(timed_out=False))
                return pos + len(_ESU)
        if _incomplete(bytes(data[pos : pos + len(_BSU)])):
            return None
        self._emit(data[pos : pos + _ONE], out)
        return pos + _ONE

    def _osc(self, data: bytearray, pos: int, out: bytearray, now: float) -> int | None:
        end = _osc_end(data, pos)
        if end is None:
            if len(data) - pos <= _OSC_HOLD:
                if self._osc_since is None:
                    self._osc_since = now
                return None
            # Too long to be a signal: pass the raw bytes on and let the terminal parse them.
            self._osc_since = None
            self._emit(data[pos:], out)
            return len(data)
        self._osc_since = None
        if _owned_osc(data, pos):
            signal = _signal_from_osc(bytes(data[pos:end]))
            if signal is not None:
                self._signals.append(signal)
            return end
        self._emit(data[pos:end], out)
        return end

    def _give_up_osc(self, out: bytearray) -> None:
        """The OSC at the front never ended: pass its bytes on unchanged."""
        self._osc_since = None
        end = len(self._pending)
        self._emit(bytes(self._pending[:end]), out)
        del self._pending[:end]

    def _emit(self, chunk: bytes | bytearray, out: bytearray) -> None:
        if not chunk:
            return
        if self._depth > _ZERO:
            self._hold(bytes(chunk), out)
            return
        out.extend(chunk)
        self.bytes_out += len(chunk)

    def _hold(self, chunk: bytes, out: bytearray) -> None:
        view = chunk
        while view:
            room = self._limit - len(self._held)
            if room <= _ZERO:
                out.extend(self._release(timed_out=False))
                room = self._limit
            take = view[:room]
            self._held.extend(take)
            view = view[len(take) :]
            if len(self._held) >= self._limit:
                out.extend(self._release(timed_out=False))

    def _release(self, *, timed_out: bool) -> bytes:
        data = bytes(self._held)
        self._held.clear()
        if timed_out:
            self.frames_timed_out += _ONE
        if data == b"":
            return b""
        self.frames += _ONE
        self.bytes_out += len(data)
        self.max_frame_bytes = max(self.max_frame_bytes, len(data))
        return data


def _incomplete(data: bytes) -> bool:
    if data == b"":
        return False
    if _BSU.startswith(data) and data != _BSU:
        return True
    if _ESU.startswith(data) and data != _ESU:
        return True
    return _OSC.startswith(data) and data != _OSC


def _next_special(data: bytearray, start: int) -> int:
    """Index of the next BEL, OSC, BSU/ESU, or marker cut off at the end, or the end of `data`.

    Other escapes (colors, cursor moves) are plain output and do not split the run.
    """
    found = _MARK.search(data, start)
    if found is not None:
        return found.start()
    limit = len(data)
    begin = max(start, limit - (len(_BSU) - _ONE))
    for pos in range(begin, limit):
        if data[pos] == _ESC and _marked(data, pos):
            return pos
    return limit


def _marked(data: bytearray, pos: int) -> bool:
    """True when `pos` is an OSC, a BSU/ESU, or a prefix of one of those cut off at the end."""
    nxt = pos + _ONE
    limit = len(data)
    if nxt >= limit:
        return True
    kind = data[nxt]
    if kind == _RIGHT_BRACKET:
        return True
    if kind != _LEFT_BRACKET:
        return False
    if data.startswith(_BSU, pos) or data.startswith(_ESU, pos):
        return True
    if limit - pos >= len(_BSU):
        return False
    return _incomplete(bytes(data[pos:]))


def _owned_osc(data: bytearray, pos: int) -> bool:
    """True for OSC 0, 2, 9 and 777, the sequences this framer turns into signals."""
    return data.startswith(_OWN_OSC, pos)


def _osc_end(data: bytearray, start: int) -> int | None:
    """Index just past the BEL or ST that ends the OSC at `start`, or None when it has not ended yet."""
    body = start + len(_OSC)
    bel = data.find(_BEL, body)
    st = data.find(_ST, body)
    if bel < _ZERO:
        return None if st < _ZERO else st + len(_ST)
    if st < _ZERO or bel < st:
        return bel + _ONE
    return st + len(_ST)


def _signal_from_osc(osc: bytes) -> Signal | None:
    body = osc[len(_OSC) :]
    if body.endswith(_ST):
        body = body[: -len(_ST)]
    elif body.endswith(bytes((_BEL,))):
        body = body[:-1]
    if not body.startswith((b"0;", b"2;", b"9;", b"777;")):
        return None
    text = body.decode("utf-8", errors="replace")
    if text.startswith(("0;", "2;")):
        return Signal(kind="osc_title", text=text[2:][:_TEXT])
    if text.startswith("9;4;"):
        return _progress(text[4:])
    if text.startswith("9;"):
        return Signal(kind="osc9_notify", text=text[2:][:_TEXT])
    if text.startswith("777;"):
        return Signal(kind="osc777_notify", text=text[4:][:_TEXT])
    return None


def _progress(text: str) -> Signal | None:
    """`state[;value]`. `text` names the state; `progress` is the value for states 1, 2 and 4 (clamped to 100)."""
    parts = text.split(";")
    if not parts[0].isdigit() or int(parts[0]) not in _PROGRESS_STATES:
        return None
    state = int(parts[0])
    value: int | None = None
    if state in _WITH_VALUE and len(parts) > _ONE and parts[1].isdigit():
        value = min(int(parts[1]), _PROGRESS_MAX)
    if state == _ONE and value is None:
        value = _ZERO
    return Signal(kind="osc9_4_progress", text=_PROGRESS_STATES[state], progress=value)
