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
_ST: Final = b"\x1b\\"
_BEL: Final = 0x07
_ESC: Final = 0x1B
LIMIT: Final = 4 * 1024 * 1024
FLUSH_S: Final = 0.05
_OSC_HOLD: Final = 4096
_OSC_WAIT_S: Final = 0.25
# BEL, an OSC start, a BSU/ESU, or the start of one of these cut off at the end of the buffer.
_SPECIAL: Final = re.compile(rb"\x07|\x1b(?:\]|\[\?2026[hl])|\x1b(?:\[(?:\?(?:2(?:0(?:2(?:6)?)?)?)?)?)?\Z")
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
        while pos < len(data):
            byte = data[pos]
            if byte == _BEL:
                self._signals.append(Signal(kind="bell"))
                step: int | None = pos + _ONE
            elif byte == _ESC and _SPECIAL.match(data, pos) is not None:
                step = self._escape(data, pos, out, now)
            else:
                step = _next_special(data, pos + _ONE)
                self._emit(bytes(data[pos:step]), out)
            if step is None:
                break
            pos = step
        del data[:pos]
        return bytes(out)

    def _escape(self, data: bytearray, pos: int, out: bytearray, now: float) -> int | None:
        """Consume the escape at `pos`. Returns the next position, or None to wait for more bytes."""
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
        if data.startswith(_OSC, pos):
            return self._osc(data, pos, out, now)
        if _incomplete(bytes(data[pos : pos + len(_BSU)])):
            return None
        self._emit(bytes(data[pos : pos + _ONE]), out)
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
            self._emit(bytes(data[pos:]), out)
            return len(data)
        self._osc_since = None
        osc = bytes(data[pos:end])
        signal = _signal_from_osc(osc)
        if signal is not None:
            self._signals.append(signal)
        elif not osc.startswith(_OWN_OSC):
            self._emit(osc, out)
        return end

    def _give_up_osc(self, out: bytearray) -> None:
        """The OSC at the front never ended: pass its bytes on unchanged."""
        self._osc_since = None
        end = len(self._pending)
        self._emit(bytes(self._pending[:end]), out)
        del self._pending[:end]

    def _emit(self, chunk: bytes, out: bytearray) -> None:
        if not chunk:
            return
        if self._depth > _ZERO:
            self._hold(chunk, out)
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
    found = _SPECIAL.search(data, start)
    return len(data) if found is None else found.start()


def _osc_end(data: bytearray, start: int) -> int | None:
    """Index just past the BEL or ST that ends the OSC at `start`, or None when it has not ended yet."""
    body = start + len(_OSC)
    bel = data.find(_BEL, body)
    st = data.find(_ST, body)
    ends: list[int] = []
    if bel >= _ZERO:
        ends.append(bel + _ONE)
    if st >= _ZERO:
        ends.append(st + len(_ST))
    return min(ends) if ends else None


def _signal_from_osc(osc: bytes) -> Signal | None:
    body = osc[len(_OSC) :]
    if body.endswith(_ST):
        body = body[: -len(_ST)]
    elif body.endswith(bytes((_BEL,))):
        body = body[:-1]
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
