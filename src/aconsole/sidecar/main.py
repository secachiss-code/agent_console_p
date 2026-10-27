"""Pty process between VTE and the agent command.

Stdin (put in raw mode, no echo) is copied to the child. The child's output is
framed and written to stdout. Terminal signals go to the core socket when it is
up, and are dropped when it is not. SIGTERM and SIGHUP are forwarded to the
child's process group. The child sets the terminal size on its pty before it
execs, so its first draw already has it. EOF or an error on a terminal stdin
(VTE went away) sends SIGHUP to the child's group; a pipe stdin at EOF is only
no longer read. EOF
or EIO on the pty stops watching it, and a child that is still alive 100 ms
later gets SIGHUP, then SIGKILL. A closed descriptor is never selected again,
so neither case spins.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import pty
import select
import signal
import socket
import struct
import sys
import termios
import time
import tty
from io import BufferedIOBase
from typing import TYPE_CHECKING, Final, final

from aconsole.sidecar.framer import FLUSH_S, Framer
from aconsole.sidecar.ipc import exited, initialize, started, stats
from aconsole.sidecar.ipc import signal as encode_signal

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_READ: Final = 65536
_STATS_S: Final = 10.0
_CONNECT_S: Final = 0.05
_USAGE: Final = "usage: python -m aconsole.sidecar -- COMMAND [ARG...]\n"
_SOCKET: Final = "ACONSOLE_SOCKET"
_TAB: Final = "ACONSOLE_TAB"
_PROOF_ENV: Final = "ACONSOLE_SIDECAR_TOKEN"
_LFLAG: Final = 3
_CC: Final = 6
_VMIN_VALUE: Final = 1
_VTIME_VALUE: Final = 0
_SIGNAL_EXIT_BASE: Final = 128
_FAILED_EXEC: Final = 127
_UNCAUGHT: Final = 1
_WINSIZE: Final = "HHHH"
_PIXELS: Final = 0
_HANGUP_GRACE_S: Final = 0.1


def main(argv: list[str], environ: Mapping[str, str] | None = None) -> int:
    """Run the child after `--`. Return its exit code, or 2 when usage is wrong."""
    command = _command(argv)
    if command is None:
        _ = sys.stderr.write(_USAGE)
        return 2
    env = os.environ if environ is None else environ
    link = CoreLink(env.get(_SOCKET), env.get(_TAB), env.get(_PROOF_ENV))
    size = _outer_size(_stdin_fd())
    pid, fd = pty.fork()
    if pid == 0:
        try:
            _set_size(pty.STDIN_FILENO, size)
            os.execvpe(command[0], command, dict(env))  # noqa: S606 -- replace this process with the child command
        except OSError:
            os._exit(_FAILED_EXEC)
    return _pump(pid, fd, link)


def exchange(argv: list[str], payload: bytes, *, timeout_s: float = 1.0) -> bytes:
    """Run `argv` on a pty, write `payload`, and return the framed output."""
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execvp(argv[0], argv)  # noqa: S606 -- replace this process with the child command
        except OSError:
            os._exit(_FAILED_EXEC)
    if payload:
        _ = os.write(fd, payload)
    framer = Framer()
    out = bytearray()
    deadline = time.monotonic() + timeout_s
    reaped = False
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            ready, _, _ = select.select([fd], [], [], min(FLUSH_S, max(0.0, remaining)))
        except InterruptedError:
            ready = []
        now = time.monotonic()
        if ready:
            chunk = _read(fd)
            if chunk is None:
                break
            out.extend(framer.feed(chunk, now))
        out.extend(framer.flush_due(now))
        finished, _status = os.waitpid(pid, os.WNOHANG)
        if finished:
            reaped = True
            break
    out.extend(_drain_fd(fd, framer))
    out.extend(framer.finish())
    os.close(fd)
    if not reaped:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)
        _reap(pid)
    return bytes(out)


def resize(fd: int, pid: int, rows: int, cols: int) -> None:
    """Set the pty size and forward SIGWINCH to the child."""
    packed = struct.pack(_WINSIZE, rows, cols, _PIXELS, _PIXELS)
    _ = fcntl_ioctl(fd, termios.TIOCSWINSZ, packed)
    os.kill(pid, signal.SIGWINCH)


def winsize(fd: int) -> tuple[int, int]:
    """Rows and columns reported by the pty."""
    blank = struct.pack(_WINSIZE, _PIXELS, _PIXELS, _PIXELS, _PIXELS)
    packed = fcntl_ioctl(fd, termios.TIOCGWINSZ, blank)
    rows, cols, _x_pixels, _y_pixels = struct.unpack(_WINSIZE, packed)
    return rows, cols


def fcntl_ioctl(fd: int, request: int, packed: bytes) -> bytes:
    """ioctl that always hands bytes back to the caller."""
    return fcntl.ioctl(fd, request, packed)


@final
class CoreLink:
    """Best-effort unix client. A failure clears the socket and returns."""

    _sock: socket.socket | None

    def __init__(self, path: str | None, tab_id: str | None, proof: str | None) -> None:
        self._sock = None
        if path is None or tab_id is None or proof is None or path == "":
            return
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(_CONNECT_S)
        try:
            sock.connect(path)
        except OSError:
            sock.close()
            return
        self._sock = sock
        self.send(initialize(tab_id, proof, os.getpid()))

    def send(self, payload: bytes) -> None:
        """Write one frame. Drop the link on any error."""
        sock = self._sock
        if sock is None:
            return
        try:
            sock.sendall(payload)
        except OSError:
            self.close()

    def close(self) -> None:
        sock = self._sock
        self._sock = None
        if sock is not None:
            sock.close()


def _pump(pid: int, fd: int, link: CoreLink) -> int:
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    link.send(started(pid, started_at))
    framer = Framer()
    restore_signals = _install_signals(pid, fd)
    restore_pty = _cbreak(fd)
    stdin_fd = _stdin_fd()
    stdin_tty = stdin_fd is not None and os.isatty(stdin_fd)
    restore_stdin = _raw(stdin_fd) if stdin_fd is not None else _noop
    last_stats = time.monotonic()
    exit_code: int | None = None
    signal_number: int | None = None
    pty_open = True
    stdin_open = stdin_fd is not None
    hangup = _Hangup(pid)
    try:
        while exit_code is None and signal_number is None:
            now = time.monotonic()
            readable = _select(fd if pty_open else None, stdin_fd if stdin_open else None)
            if pty_open and fd in readable:
                chunk = _read(fd)
                if chunk:
                    _emit(framer.feed(chunk, now), framer, link)
                else:
                    pty_open = False
                    hangup.start(now)
            if stdin_open and stdin_fd is not None and stdin_fd in readable:
                stdin_open = _forward(stdin_fd, fd, pid, hang_up=stdin_tty)
            hangup.tick(now)
            _emit(framer.flush_due(now), framer, link)
            if now - last_stats >= _STATS_S:
                link.send(stats(framer.bytes_out, framer.frames, framer.frames_timed_out, framer.max_frame_bytes))
                last_stats = now
            exit_code, signal_number = _wait(pid)
    finally:
        restore_signals()
        restore_pty()
        restore_stdin()
        _emit(_drain_fd(fd, framer), framer, link)
        _emit(framer.finish(), framer, link)
        os.close(fd)
        link.send(exited(exit_code, signal_number))
        link.close()
    if signal_number is not None:
        return _SIGNAL_EXIT_BASE + signal_number
    if exit_code is None:
        return _UNCAUGHT
    return exit_code


def _forward(stdin_fd: int, fd: int, pid: int, *, hang_up: bool) -> bool:
    """Copy one read of stdin to the pty. False at EOF; then a terminal stdin hangs up the child.

    `hang_up` is decided at start: a hung-up tty no longer reports itself as one.
    """
    typed = _read(stdin_fd)
    if typed:
        _ = os.write(fd, typed)
        return True
    if hang_up:
        _hang_up(pid)
    return False


def _emit(data: bytes, framer: Framer, link: CoreLink) -> None:
    if data:
        _write_stdout(data)
    for item in framer.take_signals():
        link.send(encode_signal(item.kind, item.text, item.progress))


def _write_stdout(data: bytes) -> None:
    buffer = sys.stdout.buffer
    if isinstance(buffer, BufferedIOBase):
        _ = buffer.write(data)
        buffer.flush()


def _command(argv: list[str]) -> list[str] | None:
    if "--" not in argv:
        return None
    command = argv[argv.index("--") + 1 :]
    if not command:
        return None
    return command


def _read(fd: int) -> bytes | None:
    try:
        chunk = os.read(fd, _READ)
    except OSError:
        return None
    if chunk == b"":
        return None
    return chunk


def _drain_fd(fd: int, framer: Framer) -> bytes:
    out = bytearray()
    while True:
        try:
            ready, _, _ = select.select([fd], [], [], 0)
        except InterruptedError, OSError, ValueError:
            return bytes(out)
        if not ready:
            return bytes(out)
        chunk = _read(fd)
        if not chunk:
            return bytes(out)
        out.extend(framer.feed(chunk, time.monotonic()))


def _stdin_fd() -> int | None:
    try:
        return sys.stdin.fileno()
    except AttributeError, OSError, ValueError:
        return None


def _select(*fds: int | None) -> list[int]:
    """Wait up to one flush period for the open descriptors. With none open, this only sleeps."""
    watch = [fd for fd in fds if fd is not None]
    try:
        ready, _, _ = select.select(watch, [], [], FLUSH_S)
    except InterruptedError, OSError, ValueError:
        return []
    return ready


def _hang_up(pid: int) -> None:
    with contextlib.suppress(OSError):
        os.killpg(pid, signal.SIGHUP)


@final
class _Hangup:
    """After the pty closed: SIGHUP the child's group after 100 ms, SIGKILL after 200 ms."""

    def __init__(self, pid: int) -> None:
        self._pid = pid
        self._since: float | None = None
        self._step = 0

    def start(self, now: float) -> None:
        if self._since is None:
            self._since = now

    def tick(self, now: float) -> None:
        since = self._since
        if since is None:
            return
        due = int((now - since) / _HANGUP_GRACE_S)
        if due >= 1 and self._step < 1:
            self._step = 1
            _hang_up(self._pid)
        if due >= 2 and self._step < 2:  # noqa: PLR2004 -- second grace period
            self._step = 2
            with contextlib.suppress(OSError):
                os.killpg(self._pid, signal.SIGKILL)


def _outer_size(source: int | None) -> tuple[int, int] | None:
    """The outer terminal's rows and columns, or None when stdin is not a sized terminal."""
    if source is None or not os.isatty(source):
        return None
    try:
        rows, cols = winsize(source)
    except OSError:
        return None
    if rows > 0 and cols > 0:
        return rows, cols
    return None


def _set_size(fd: int, size: tuple[int, int] | None) -> None:
    """Runs in the child before exec: the pty has the outer size before the command can read it."""
    if size is None:
        return
    packed = struct.pack(_WINSIZE, size[0], size[1], _PIXELS, _PIXELS)
    with contextlib.suppress(OSError):
        _ = fcntl_ioctl(fd, termios.TIOCSWINSZ, packed)


def _raw(fd: int) -> Callable[[], None]:
    """Raw mode for the outer terminal: no echo, no line editing, no signal keys; bytes go to the child as typed."""
    if not os.isatty(fd):
        return _noop
    previous = termios.tcgetattr(fd)
    attrs = termios.tcgetattr(fd)
    tty.cfmakeraw(attrs)
    termios.tcsetattr(fd, termios.TCSANOW, attrs)

    def restore() -> None:
        # A hung-up terminal answers EIO; the cleanup after this must still run.
        with contextlib.suppress(termios.error, OSError):
            termios.tcsetattr(fd, termios.TCSADRAIN, previous)

    return restore


def _wait(pid: int) -> tuple[int | None, int | None]:
    finished, status = os.waitpid(pid, os.WNOHANG)
    if not finished:
        return None, None
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status), None
    if os.WIFSIGNALED(status):
        return None, os.WTERMSIG(status)
    return _UNCAUGHT, None


def _reap(pid: int) -> None:
    with contextlib.suppress(ChildProcessError):
        _ = os.waitpid(pid, 0)


def _cbreak(fd: int) -> Callable[[], None]:
    if not os.isatty(fd):
        return _noop
    previous = termios.tcgetattr(fd)
    attrs = termios.tcgetattr(fd)
    _clear_icanon(attrs)
    termios.tcsetattr(fd, termios.TCSANOW, attrs)

    def restore() -> None:
        # A hung-up terminal answers EIO; the cleanup after this must still run.
        with contextlib.suppress(termios.error, OSError):
            termios.tcsetattr(fd, termios.TCSADRAIN, previous)

    return restore


def _noop() -> None:
    return None


def _clear_icanon(attrs: list[object]) -> None:
    flag = attrs[_LFLAG]
    cc = attrs[_CC]
    if isinstance(flag, int):
        attrs[_LFLAG] = flag & ~termios.ICANON
    if isinstance(cc, list):
        cc[termios.VMIN] = _VMIN_VALUE
        cc[termios.VTIME] = _VTIME_VALUE


def _install_signals(pid: int, fd: int) -> Callable[[], None]:
    def stop(signum: int, _frame: object) -> None:
        with contextlib.suppress(OSError):
            os.killpg(pid, signum)

    def winch(_signum: int, _frame: object) -> None:
        source = _stdin_fd()
        if source is None or not os.isatty(source):
            return
        rows, cols = winsize(source)
        resize(fd, pid, rows, cols)

    previous_term = signal.signal(signal.SIGTERM, stop)
    previous_hup = signal.signal(signal.SIGHUP, stop)
    previous_winch = signal.signal(signal.SIGWINCH, winch)

    def restore() -> None:
        _ = signal.signal(signal.SIGTERM, previous_term)
        _ = signal.signal(signal.SIGHUP, previous_hup)
        _ = signal.signal(signal.SIGWINCH, previous_winch)

    return restore
