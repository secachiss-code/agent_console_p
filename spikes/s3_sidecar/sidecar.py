"""Pty sidecar prototype: raw stdin, BSU/ESU hold, SIGWINCH, signal forward."""

from __future__ import annotations

import os
import pty
import select
import signal
import struct
import termios
import time
import tty
from collections.abc import Callable
from dataclasses import dataclass, field

BSU = b"\x1b[?2026h"
ESU = b"\x1b[?2026l"
LIMIT = 4 * 1024 * 1024
FLUSH_S = 0.05


@dataclass
class Scanner:
    """Hold bytes between DEC 2026 BSU and ESU. Carry a split marker tail."""

    in_sync: bool = False
    held: bytearray = field(default_factory=bytearray)
    pending: bytearray = field(default_factory=bytearray)
    signals: list[bytes] = field(default_factory=list)
    held_since: float = 0.0

    def feed(self, data: bytes, now: float | None = None) -> bytes:
        self.pending.extend(data)
        return self._drain(now if now is not None else time.monotonic())

    def flush_due(self, now: float | None = None) -> bytes:
        moment = now if now is not None else time.monotonic()
        if self.in_sync and self.held and moment - self.held_since >= FLUSH_S:
            return self._release()
        return b""

    def _drain(self, now: float) -> bytes:
        out = bytearray()
        while self.pending:
            if self.pending.startswith(BSU):
                del self.pending[: len(BSU)]
                self.in_sync = True
                self.held_since = now
                continue
            if self.pending.startswith(ESU):
                del self.pending[: len(ESU)]
                self.in_sync = False
                out.extend(self._release())
                continue
            if self.pending.startswith(b"\x1b]"):
                osc = _take_osc(self.pending)
                if osc is None and len(self.pending) < 4096:
                    break
                if osc is not None:
                    self.signals.append(osc)
                    continue
            marker = _prefix_hold(bytes(self.pending))
            if marker:
                break
            if self.pending[:1] == b"\x07":
                self.signals.append(b"\x07")
                del self.pending[:1]
                continue
            byte = self.pending[0:1]
            del self.pending[:1]
            if self.in_sync:
                self.held.extend(byte)
                if len(self.held) >= LIMIT:
                    out.extend(self._release())
            else:
                out.extend(byte)
        return bytes(out)

    def _release(self) -> bytes:
        data = bytes(self.held)
        self.held.clear()
        return data


def _prefix_hold(data: bytes) -> bool:
    for marker in (BSU, ESU):
        if marker.startswith(data) and data != marker:
            return True
    return data.startswith(b"\x1b") and len(data) < len(BSU)


def _take_osc(buf: bytearray) -> bytes | None:
    if not buf.startswith(b"\x1b]"):
        return None
    for index in range(2, len(buf)):
        if buf[index] == 0x07:
            osc = bytes(buf[: index + 1])
            del buf[: index + 1]
            return osc
        if buf[index] == 0x1B and index + 1 < len(buf) and buf[index + 1] == 0x5C:
            osc = bytes(buf[: index + 2])
            del buf[: index + 2]
            return osc
    if len(buf) > 4096:
        return None
    return None


def set_winsize(fd: int, rows: int, cols: int) -> None:
    packed = struct.pack("HHHH", rows, cols, 0, 0)
    import fcntl

    fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)


def get_winsize(fd: int) -> tuple[int, int]:
    import fcntl

    packed = fcntl.ioctl(fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
    rows, cols, _x, _y = struct.unpack("HHHH", packed)
    return rows, cols


def raw_stdin(fd: int) -> Callable[[], None]:
    if not os.isatty(fd):
        return lambda: None
    previous = termios.tcgetattr(fd)
    tty.setraw(fd)

    def restore() -> None:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)

    return restore


@dataclass
class Child:
    pid: int
    fd: int
    scanner: Scanner = field(default_factory=Scanner)
    output: bytearray = field(default_factory=bytearray)

    def read_ready(self) -> None:
        try:
            chunk = os.read(self.fd, 65536)
        except OSError:
            return
        if not chunk:
            return
        self.output.extend(self.scanner.feed(chunk))
        self.output.extend(self.scanner.flush_due())

    def write(self, data: bytes) -> None:
        os.write(self.fd, data)

    def resize(self, rows: int, cols: int) -> None:
        set_winsize(self.fd, rows, cols)
        os.kill(self.pid, signal.SIGWINCH)

    def forward(self, sig: int) -> None:
        os.kill(self.pid, sig)

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            return


def cbreak(fd: int) -> None:
    """Leave ISIG on so Ctrl+C interrupts, and turn off canonical mode so Ctrl+P arrives."""
    attrs = termios.tcgetattr(fd)
    attrs[3] = attrs[3] & ~termios.ICANON
    attrs[6][termios.VMIN] = 1
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def spawn(argv: list[str], env: dict[str, str] | None = None) -> Child:
    pid, fd = pty.fork()
    if pid == 0:
        if env is not None:
            os.execvpe(argv[0], argv, env)
        os.execvp(argv[0], argv)
        raise OSError(argv[0])
    cbreak(fd)
    return Child(pid=pid, fd=fd)


def pump(child: Child, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        timeout = min(FLUSH_S, deadline - time.monotonic())
        readable, _, _ = select.select([child.fd], [], [], max(0.0, timeout))
        if readable:
            child.read_ready()
        else:
            child.output.extend(child.scanner.flush_due())
        finished, _status = os.waitpid(child.pid, os.WNOHANG)
        if finished:
            child.read_ready()
            return
