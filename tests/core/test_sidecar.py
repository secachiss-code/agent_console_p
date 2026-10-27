"""Sidecar framer, pty, and the NDJSON the core decodes."""

from __future__ import annotations

import os
import pty
import runpy
import signal
import socket
import sys
import time
from pathlib import Path

import msgspec
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aconsole.contracts.ipc import (
    InitializeParams,
    Notification,
    Request,
    SidecarExited,
    SidecarSignal,
    SidecarStarted,
    SidecarStats,
)
from aconsole.core.infrastructure.ipc.framing import decode_envelope
from aconsole.sidecar.framer import LIMIT, Framer, Signal
from aconsole.sidecar.ipc import exited, initialize, started, stats
from aconsole.sidecar.ipc import signal as encode_signal
from aconsole.sidecar.main import CoreLink, exchange, main, resize, winsize

_BSU = b"\x1b[?2026h"
_ESU = b"\x1b[?2026l"
_NOW = 0.0
_FLUSH = 0.05
_EXAMPLES = 40
_BENCH_LIMIT_S = 0.005
_BENCH_BYTES = 64
_TITLE = 512
_LATE = 0.4
_CHILD_SLEEP = "5"


def _chunks(payload: bytes, cuts: list[int]) -> list[bytes]:
    points = sorted({cut for cut in cuts if _NOW <= cut <= len(payload)})
    pieces: list[bytes] = []
    start = 0
    for point in points:
        pieces.append(payload[start:point])
        start = point
    pieces.append(payload[start:])
    return pieces


def _framed(payload: bytes, pieces: list[bytes]) -> tuple[bytes, tuple[Signal, ...], bytes, tuple[Signal, ...]]:
    whole = Framer()
    expected = bytearray(whole.feed(payload, _NOW))
    expected.extend(whole.finish())
    split = Framer()
    got = bytearray()
    for piece in pieces:
        got.extend(split.feed(piece, _NOW))
    got.extend(split.finish())
    return bytes(got), split.take_signals(), bytes(expected), whole.take_signals()


def _value_error(limit: int, flush_s: float) -> str:
    try:
        _ = Framer(limit=limit, flush_s=flush_s)
    except ValueError as exc:
        return str(exc)
    return ""


def _lines(blob: bytes) -> list[bytes]:
    return [line + b"\n" for line in blob.split(b"\n") if line]


def _serve(path: str) -> socket.socket:
    socket_path = Path(path)
    if socket_path.exists():
        socket_path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    return server


def _recv_all(conn: socket.socket) -> bytes:
    conn.settimeout(1.0)
    blob = bytearray()
    while True:
        try:
            piece = conn.recv(65536)
        except TimeoutError:
            return bytes(blob)
        if piece == b"":
            return bytes(blob)
        blob.extend(piece)


@given(payload=st.binary(max_size=48), cuts=st.lists(st.integers(min_value=0, max_value=48), max_size=8))
@settings(max_examples=_EXAMPLES, deadline=None)
def test_sidecar_any_split_matches(payload: bytes, cuts: list[int]) -> None:
    got, got_signals, expected, expected_signals = _framed(payload, _chunks(payload, cuts))
    assert got == expected
    assert got_signals == expected_signals


def test_sidecar_nested_bsu_eof_timeout_and_limit() -> None:
    nested = Framer()
    assert nested.feed(_BSU + _BSU + b"IN" + _ESU + b"MID" + _ESU + b"OUT", _NOW) == b"INMIDOUT"
    assert _BSU not in nested.feed(b"", _NOW)

    eof = Framer()
    assert eof.feed(_BSU + b"HELLO" + _BSU[:4], _NOW) == b""
    assert eof.finish() == b"HELLO"

    timed = Framer()
    assert timed.feed(_BSU + b"HI", _NOW) == b""
    assert timed.flush_due(_FLUSH - 0.001) == b""
    assert timed.flush_due(_FLUSH) == b"HI"
    assert timed.frames_timed_out == 1
    assert timed.feed(b"MORE", _FLUSH) == b""
    assert timed.flush_due(_FLUSH + _FLUSH) == b"MORE"
    assert timed.frames_timed_out == 2

    limited = Framer(limit=4)
    assert limited.feed(_BSU + b"abcdefghij", _NOW) == b"abcdefgh"
    assert limited.finish() == b"ij"
    assert limited.frames_timed_out == 0

    full = Framer()
    released = full.feed(_BSU + b"x" * LIMIT, _NOW)
    assert released == b"x" * LIMIT
    assert full.frames == 1
    assert full.frames_timed_out == 0
    assert full.max_frame_bytes == LIMIT


def test_sidecar_signals_and_split_markers() -> None:
    framer = Framer()
    title = "привет"
    payload = (
        _ESU
        + b"OK"
        + b"\x1b]0;"
        + title.encode()
        + b"\x07"
        + b"\x07"
        + b"\x1b]9;note\x07"
        + b"\x1b]777;n\x1b\\"
        + b"\x1b]9;4;1;40\x07"
        + b"\x1b]9;4;150\x07"
        + b"\x1b]99;x\x07"
        + b"\x1b]2;"
        + (b"y" * 600)
        + b"\x07"
        + b"Z"
    )
    assert framer.feed(payload, _NOW) == b"OK\x1b]99;x\x07Z"
    found = framer.take_signals()
    assert found[0] == Signal(kind="osc_title", text=title)
    assert found[1] == Signal(kind="bell")
    assert found[2] == Signal(kind="osc9_notify", text="note")
    assert found[3] == Signal(kind="osc777_notify", text="n")
    assert found[4] == Signal(kind="osc9_4_progress", text="value", progress=40)
    assert found[5] == Signal(kind="osc_title", text="y" * _TITLE)
    assert len(found) == 6
    assert framer.take_signals() == ()
    overflow = Framer()
    assert overflow.feed(b"\x1b]" + (b"q" * 5000), _NOW).startswith(b"\x1b]")

    split = Framer()
    assert split.feed(_BSU[:4], _NOW) == b""
    assert split.feed(_BSU[4:] + b"Z" + _ESU, _NOW) == b"Z"
    assert _value_error(0, _FLUSH) == "frame limit must be positive"
    assert _value_error(4, _NOW) == "frame timeout must be positive"


def test_sidecar_frame_latency_is_under_five_milliseconds() -> None:
    framer = Framer()
    payload = _BSU + (b"x" * _BENCH_BYTES) + _ESU
    started_at = time.perf_counter()
    out = framer.feed(payload, _NOW)
    elapsed = time.perf_counter() - started_at
    assert out == b"x" * _BENCH_BYTES
    assert elapsed < _BENCH_LIMIT_S
    assert framer.frames == 1


def test_sidecar_echoes_through_a_pty() -> None:
    echoed = exchange(["/bin/sh", "-c", 'read x; printf %s "$x"'], b"ping\n", timeout_s=2.0)
    framed = exchange(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\x1b[?2026hHELLO\\x1b[?2026l')"],
        b"",
        timeout_s=2.0,
    )
    assert b"ping" in echoed
    assert framed == b"HELLO"


def test_sidecar_json_decodes_with_msgspec() -> None:
    started_line = started(7, "2026-10-27T09:17:18+00:00")
    started_env = decode_envelope(started_line)
    assert isinstance(started_env, Notification)
    started_body = msgspec.json.decode(bytes(started_env.params), type=SidecarStarted)
    assert started_body.child_pid == 7
    assert started_body.started_at.tzinfo is not None

    signal_line = encode_signal("osc9_4_progress", None, 40)
    signal_env = decode_envelope(signal_line)
    assert isinstance(signal_env, Notification)
    signal_body = msgspec.json.decode(bytes(signal_env.params), type=SidecarSignal)
    assert signal_body.kind == "osc9_4_progress"
    assert signal_body.progress == 40
    assert b"text" not in signal_line

    stats_env = decode_envelope(stats(3, 1, 0, 3))
    assert isinstance(stats_env, Notification)
    stats_body = msgspec.json.decode(bytes(stats_env.params), type=SidecarStats)
    assert stats_body.bytes_out == 3
    assert stats_body.max_frame_bytes == 3

    exited_env = decode_envelope(exited(None, 15))
    assert isinstance(exited_env, Notification)
    exited_body = msgspec.json.decode(bytes(exited_env.params), type=SidecarExited)
    assert exited_body.exit_code is None
    assert exited_body.signal == 15

    init_env = decode_envelope(initialize("tab-1", "shown-to-user", 11))
    assert isinstance(init_env, Request)
    assert init_env.id == 1
    init_body = msgspec.json.decode(bytes(init_env.params), type=InitializeParams)
    assert init_body.role == "sidecar"
    assert init_body.pid == 11
    handed = init_body.sidecar_token
    assert handed == "shown-to-user"
    plain = encode_signal("osc_title", "привет", None)
    assert "привет" in plain.decode()


def test_sidecar_usage_resize_and_closed_socket(tmp_path: Path) -> None:
    assert main([]) == 2
    assert main(["--"]) == 2
    pid, fd = pty.fork()
    if pid == 0:
        time.sleep(2)
        os._exit(0)
    try:
        resize(fd, pid, 40, 120)
        assert winsize(fd) == (40, 120)
    finally:
        os.kill(pid, signal.SIGKILL)
        os.close(fd)
        _ = os.waitpid(pid, 0)

    missing = CoreLink(str(tmp_path / "missing.sock"), "tab-1", "shown-to-user")
    assert missing.send(b"nope\n") is None
    blank = CoreLink("", None, None)
    assert blank.send(b"nope\n") is None

    path = str(tmp_path / "s.sock")
    server = _serve(path)
    link = CoreLink(path, "tab-1", "shown-to-user")
    accepted = server.accept()
    conn = accepted[0]
    conn.settimeout(1.0)
    first = conn.recv(65536)
    conn.close()
    assert link.send(b"x\n") is None
    link.close()
    server.close()
    assert b'"method":"initialize"' in first
    assert b"shown-to-user" in first


def test_sidecar_main_notifies_core_and_forwards_signals(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import aconsole.sidecar.main as sidecar_main

    monkeypatch.setattr(sidecar_main, "_STATS_S", 0.0)
    path = str(tmp_path / "core.sock")
    server = _serve(path)
    env = {
        "ACONSOLE_SOCKET": path,
        "ACONSOLE_TAB": "tab-1",
        "ACONSOLE_SIDECAR_TOKEN": "shown-to-user",
    }
    code = main(["--", "/bin/true"], environ=env)
    accepted = server.accept()
    conn = accepted[0]
    blob = _recv_all(conn)
    conn.close()
    server.close()
    methods = [line for line in _lines(blob) if b"sidecar/" in line or b"initialize" in line]
    assert code == 0
    assert any(b"sidecar/started" in line for line in methods)
    assert any(b"sidecar/stats" in line for line in methods)
    assert any(b"sidecar/exited" in line for line in methods)
    assert any(b'"method":"initialize"' in line for line in methods)

    def _alarm(_signum: int, _frame: object) -> None:
        os.kill(os.getpid(), signal.SIGWINCH)
        os.kill(os.getpid(), signal.SIGTERM)

    previous_alarm = signal.signal(signal.SIGALRM, _alarm)
    _ = signal.setitimer(signal.ITIMER_REAL, _LATE)
    try:
        forwarded = main(["--", "/bin/sh", "-c", f"sleep {_CHILD_SLEEP}"], environ={"PATH": "/usr/bin:/bin"})
    finally:
        _ = signal.setitimer(signal.ITIMER_REAL, 0.0)
        _ = signal.signal(signal.SIGALRM, previous_alarm)
    assert forwarded == 128 + signal.SIGTERM


def test_sidecar_copies_stdin_and_module_entry(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    read_fd, write_fd = os.pipe()
    _ = os.write(write_fd, b"ping\n")
    os.close(write_fd)
    stdin = os.fdopen(read_fd, "rb", buffering=0)
    monkeypatch.setattr(sys, "stdin", stdin)
    try:
        code = main(["--", "/bin/sh", "-c", 'read x; printf %s "$x"'])
        captured = capsys.readouterr()
    finally:
        stdin.close()
    assert code == 0
    assert "ping" in captured.out

    monkeypatch.setattr(sys, "argv", ["aconsole.sidecar"])
    with pytest.raises(SystemExit, match="2") as caught:
        _ = runpy.run_module("aconsole.sidecar", run_name="__main__")
    assert caught.value.code == 2
