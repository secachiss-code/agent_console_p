"""Round 6 K2.4: sidecar framer scan cost, OSC 9;4 and hyperlink handling, and the pty hangup and size paths."""

from __future__ import annotations

import fcntl
import os
import pty
import shlex
import signal
import struct
import sys
import termios
import time
from typing import TYPE_CHECKING, Final

import pytest

from aconsole.sidecar.framer import Framer, Signal
from aconsole.sidecar.main import main

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_BSU: Final = b"\x1b[?2026h"
_ESU: Final = b"\x1b[?2026l"
_NOW: Final = 0.0
_KIB: Final = 1024
_MIB: Final = 1024 * _KIB
_SMALL: Final = 64 * _KIB
_CHUNK: Final = 4 * _KIB
_REPEAT: Final = 5
_TARGET_S: Final = 0.005
_CEILING_S: Final = 0.015
_RATIO: Final = 25.0
_UNIT: Final = b"\x1b[31mred\x1b[0m plain " + _BSU + b"frame" + _ESU + b"\x07"
_OSC_WAIT_S: Final = 0.25
_OSC_LIMIT: Final = 4096
_HANGUP_CEILING_S: Final = 0.6
_HANGUP_OUTER_CEILING_S: Final = 1.0
_CPU_CEILING_S: Final = 0.2
_OUTER_AT_S: Final = 0.2
_TIMER_OFF: Final = 0.0
_LFLAG: Final = 3
_WINSIZE: Final = "HHHH"
_ROWS: Final = 33
_COLS: Final = 101
_HUP_STATUS: Final = 128 + signal.SIGHUP


def _mixed(size: int) -> bytes:
    """Colors, plain text, BSU/ESU pairs and BEL, repeated to `size` bytes."""
    return _UNIT * (size // len(_UNIT))


def _feed_once(payload: bytes) -> None:
    framer = Framer()
    _ = framer.feed(payload, _NOW)
    _ = framer.take_signals()


def _feed_chunked(payload: bytes, chunk: int) -> None:
    framer = Framer()
    for start in range(0, len(payload), chunk):
        _ = framer.feed(payload[start : start + chunk], _NOW)
    _ = framer.finish()
    _ = framer.take_signals()


def _best_of(run: Callable[[], None]) -> float:
    """Best CPU time of `_REPEAT` runs. CPU time, not wall time, so a busy neighbour does not inflate it."""
    best = float("inf")
    for _ in range(_REPEAT):
        started = time.process_time()
        run()
        best = min(best, time.process_time() - started)
    return best


def _lflag(fd: int) -> int:
    """Local mode flags of a terminal (ECHO, ICANON, ...)."""
    attrs: list[object] = termios.tcgetattr(fd)
    flag = attrs[_LFLAG]
    assert isinstance(flag, int)
    return flag


def test_sidecar_scan_of_64_kib_is_fast_enough() -> None:
    payload = _mixed(_SMALL)
    best = _best_of(lambda: _feed_once(payload))
    assert best <= _CEILING_S, f"64 KiB scan best of {_REPEAT}: {best * 1000:.2f} ms (target {_TARGET_S * 1000:.0f} ms)"


def test_sidecar_scan_is_linear_not_quadratic() -> None:
    small = _mixed(_SMALL)
    large = _mixed(_MIB)
    base = _best_of(lambda: _feed_chunked(small, _CHUNK))
    grown = _best_of(lambda: _feed_chunked(large, _CHUNK))
    assert grown <= _RATIO * base, (
        f"1 MiB in 4 KiB chunks: {grown * 1000:.1f} ms, 64 KiB: {base * 1000:.2f} ms, "
        f"ratio {grown / base:.1f} (limit {_RATIO:.0f})"
    )


def test_sidecar_unterminated_osc_waits_then_passes_raw() -> None:
    framer = Framer()
    assert framer.feed(b"A\x1b]0;tit", _NOW) == b"A"
    assert framer.flush_due(_OSC_WAIT_S - 0.001) == b""
    assert framer.flush_due(_OSC_WAIT_S) == b"\x1b]0;tit"
    assert framer.feed(b"B\x1b[31mC", 0.3) == b"B\x1b[31mC"
    assert framer.take_signals() == ()


@pytest.mark.parametrize("number", [b"7", b"0"])
def test_sidecar_long_unterminated_osc_passes_raw_at_once(number: bytes) -> None:
    osc = b"\x1b]" + number + b";" + b"p" * (_OSC_LIMIT + 1)
    framer = Framer()
    assert framer.feed(osc, _NOW) == osc
    assert framer.take_signals() == ()
    assert framer.flush_due(_OSC_WAIT_S) == b""


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"\x1b]9;4;1;40\x07", Signal(kind="osc9_4_progress", text="value", progress=40)),
        (b"\x1b]9;4;1\x07", Signal(kind="osc9_4_progress", text="value", progress=0)),
        (b"\x1b]9;4;1;150\x07", Signal(kind="osc9_4_progress", text="value", progress=100)),
        (b"\x1b]9;4;0\x07", Signal(kind="osc9_4_progress", text="remove", progress=None)),
        (b"\x1b]9;4;2;30\x1b\\", Signal(kind="osc9_4_progress", text="error", progress=30)),
        (b"\x1b]9;4;3\x07", Signal(kind="osc9_4_progress", text="indeterminate", progress=None)),
        (b"\x1b]9;4;4;70\x07", Signal(kind="osc9_4_progress", text="paused", progress=70)),
    ],
)
def test_sidecar_osc9_4_progress_states(payload: bytes, expected: Signal) -> None:
    framer = Framer()
    assert framer.feed(payload, _NOW) == b""
    assert framer.take_signals() == (expected,)


@pytest.mark.parametrize("payload", [b"\x1b]9;4;9\x07", b"\x1b]9;4;x\x07", b"\x1b]9;4;150\x07"])
def test_sidecar_osc9_4_unknown_or_malformed_is_dropped(payload: bytes) -> None:
    framer = Framer()
    assert framer.feed(payload + b"Z", _NOW) == b"Z"
    assert framer.take_signals() == ()


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"\x1b]0;title\x07", Signal(kind="osc_title", text="title")),
        (b"\x1b]2;title\x1b\\", Signal(kind="osc_title", text="title")),
        (b"\x1b]9;hi\x07", Signal(kind="osc9_notify", text="hi")),
        (b"\x1b]777;n\x1b\\", Signal(kind="osc777_notify", text="n")),
    ],
)
def test_sidecar_osc_0_2_9_777_are_removed_as_signals(payload: bytes, expected: Signal) -> None:
    framer = Framer()
    assert framer.feed(payload, _NOW) == b""
    assert framer.take_signals() == (expected,)


def test_sidecar_hyperlink_and_shell_marks_pass_through_byte_exact() -> None:
    link = b"\x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\"
    framer = Framer()
    assert framer.feed(link, _NOW) == link
    for osc in (b"\x1b]7;file:///home/u\x07", b"\x1b]133;A\x07", b"\x1b]99;x\x07"):
        assert framer.feed(osc, _NOW) == osc
    assert framer.take_signals() == ()


def test_sidecar_markers_split_at_every_point_match_whole_feed() -> None:
    payload = (
        _BSU
        + b"A"
        + b"\x1b[31mR\x1b[0m"
        + b"\x1b]8;;u\x1b\\L\x1b]8;;\x1b\\"
        + b"\x07"
        + b"\x1b]9;4;1;5\x07"
        + _ESU
        + b"Z"
    )
    expected_out = b"A\x1b[31mR\x1b[0m\x1b]8;;u\x1b\\L\x1b]8;;\x1b\\Z"
    expected_signals = (
        Signal(kind="bell"),
        Signal(kind="osc9_4_progress", text="value", progress=5),
    )
    whole = Framer()
    assert whole.feed(payload, _NOW) == expected_out
    assert whole.finish() == b""
    assert whole.take_signals() == expected_signals
    for cut in range(len(payload) + 1):
        split = Framer()
        got = bytearray(split.feed(payload[:cut], _NOW))
        got.extend(split.feed(payload[cut:], _NOW))
        got.extend(split.finish())
        assert bytes(got) == expected_out, f"cut at {cut}"
        assert split.take_signals() == expected_signals, f"cut at {cut}"


def test_sidecar_partial_bsu_and_esu_markers_still_hold() -> None:
    framer = Framer()
    assert framer.feed(b"\x1b[?20", _NOW) == b""
    assert framer.feed(b"26hX\x1b[?202", _NOW) == b""
    assert framer.feed(b"6lY", _NOW) == b"XY"


def _pid_writer_command(pid_file: Path) -> list[str]:
    """Child command: record its pid, then sleep as the same process."""
    return ["/bin/sh", "-c", f"echo $$ > {shlex.quote(str(pid_file))}; exec sleep 5"]


def test_sidecar_pty_eof_returns_promptly_without_spinning(monkeypatch: pytest.MonkeyPatch) -> None:
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    stdin = os.fdopen(read_fd, "rb", buffering=0)
    monkeypatch.setattr(sys, "stdin", stdin)
    cpu_before = time.process_time()
    wall_before = time.monotonic()
    try:
        code = main(["--", "/bin/sh", "-c", "exec 0<&- 1>&- 2>&-; sleep 3"])
    finally:
        stdin.close()
    wall = time.monotonic() - wall_before
    cpu = time.process_time() - cpu_before
    assert code == _HUP_STATUS
    assert wall <= _HANGUP_CEILING_S, f"wall {wall:.3f} s"
    assert cpu <= _CPU_CEILING_S, f"cpu {cpu:.3f} s"


def test_sidecar_terminal_stdin_eof_hangs_up_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    outer_master, outer_slave = pty.openpty()
    closed: list[bool] = []

    def _close_outer(_signum: int, _frame: object) -> None:
        os.close(outer_master)
        closed.append(True)

    stdin = os.fdopen(outer_slave, "rb", buffering=0)
    monkeypatch.setattr(sys, "stdin", stdin)
    pid_file = tmp_path / "child.pid"
    previous = signal.signal(signal.SIGALRM, _close_outer)
    _ = signal.setitimer(signal.ITIMER_REAL, _OUTER_AT_S)
    started = time.monotonic()
    try:
        code = main(["--", *_pid_writer_command(pid_file)])
    finally:
        _ = signal.setitimer(signal.ITIMER_REAL, _TIMER_OFF)
        _ = signal.signal(signal.SIGALRM, previous)
        stdin.close()
        if not closed:
            os.close(outer_master)
    elapsed = time.monotonic() - started
    child = int(pid_file.read_text(encoding="utf-8"))
    assert code == _HUP_STATUS
    assert elapsed <= _HANGUP_OUTER_CEILING_S, f"elapsed {elapsed:.3f} s"
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_sidecar_child_starts_with_outer_terminal_size(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The child sets the size before exec, so `stty size` right away already sees it (it read "0 0" under load)."""
    outer_master, outer_slave = pty.openpty()
    _ = fcntl.ioctl(outer_slave, termios.TIOCSWINSZ, struct.pack(_WINSIZE, _ROWS, _COLS, 0, 0))
    stdin = os.fdopen(outer_slave, "rb", buffering=0)
    monkeypatch.setattr(sys, "stdin", stdin)
    try:
        code = main(["--", "/bin/sh", "-c", "stty size"])
        captured = capsys.readouterr()
    finally:
        stdin.close()
        os.close(outer_master)
    assert code == 0
    assert f"{_ROWS} {_COLS}" in captured.out


def test_sidecar_outer_terminal_is_raw_only_while_child_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outer_master, outer_slave = pty.openpty()
    before = _lflag(outer_slave)
    assert before & (termios.ECHO | termios.ICANON) == termios.ECHO | termios.ICANON
    sampled: list[int] = []

    def _sample(_signum: int, _frame: object) -> None:
        sampled.append(_lflag(outer_slave))

    stdin = os.fdopen(outer_slave, "rb", buffering=0)
    monkeypatch.setattr(sys, "stdin", stdin)
    previous = signal.signal(signal.SIGALRM, _sample)
    _ = signal.setitimer(signal.ITIMER_REAL, _OUTER_AT_S)
    try:
        code = main(["--", "/bin/sh", "-c", "sleep 0.5"])
        after = _lflag(outer_slave)
    finally:
        _ = signal.setitimer(signal.ITIMER_REAL, _TIMER_OFF)
        _ = signal.signal(signal.SIGALRM, previous)
        stdin.close()
        os.close(outer_master)
    assert code == 0
    assert sampled, "the sampler did not run while the child was alive"
    assert sampled[0] & (termios.ECHO | termios.ICANON) == 0
    assert after == before
