"""64 KiB of typical terminal output is framed within 5 ms of CPU time."""

from __future__ import annotations

import time
from typing import Final

from aconsole.sidecar.framer import Framer

_KIB: Final = 1024
_SIZE: Final = 64 * _KIB
_RUNS: Final = 5
_LIMIT_S: Final = 0.005
_NOW: Final = 0.0
# Plain text, a CSI color, OSC 0 (title), OSC 8 (hyperlink), OSC 133 (shell integration).
_UNIT: Final = (
    b"plain text line\r\n"
    b"\x1b[31mcolored\x1b[0m "
    b"\x1b]0;window title\x07"
    b"\x1b]8;;https://example.test/path\x1b\\"
    b"\x1b]133;A\x07"
)


def _payload() -> bytes:
    repeats = (_SIZE // len(_UNIT)) + 1
    return (_UNIT * repeats)[:_SIZE]


def _parse(payload: bytes) -> None:
    framer = Framer()
    _ = framer.feed(payload, _NOW)
    _ = framer.take_signals()


def test_framer_speed_64kib() -> None:
    payload = _payload()
    assert len(payload) == _SIZE
    best = min(_once(payload) for _ in range(_RUNS))
    assert best <= _LIMIT_S


def _once(payload: bytes) -> float:
    started = time.process_time()
    _parse(payload)
    return time.process_time() - started
