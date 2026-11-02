"""Where live limits come from: `claude auth status` and the Codex app server, both inside their containers."""

from __future__ import annotations

import contextlib
import os
import select
import signal
import subprocess
import time
from collections.abc import Callable
from typing import Final, final

import msgspec

_AUTH_TTL_S: Final = 300.0
_ASK_TIMEOUT_S: Final = 10.0
_READ_BYTES: Final = 65536
_RATE_LIMITS_ID: Final = 2
_ASK: Final = b"".join(
    (
        b'{"id":1,"method":"initialize","params":{"clientInfo":{"name":"aconsole","version":"0.1.0"}}}\n',
        b'{"method":"initialized"}\n',
        b'{"id":2,"method":"account/rateLimits/read"}\n',
    ),
)


class _Reply(msgspec.Struct, frozen=True):
    id: int | None = None


@final
class CachedAuth:
    """`ClaudeLimits` asks on every statusline; the account changes rarely and a container exec is slow."""

    _source: Callable[[tuple[str, ...]], bytes]
    _ttl_s: float
    _body: bytes
    _until: float

    def __init__(self, source: Callable[[tuple[str, ...]], bytes], *, ttl_s: float = _AUTH_TTL_S) -> None:
        self._source = source
        self._ttl_s = ttl_s
        self._body = b""
        self._until = 0.0

    def run(self, argv: tuple[str, ...]) -> bytes:
        now = time.monotonic()
        if self._body and now < self._until:
            return self._body
        body = self._source(argv)
        if body:
            self._body = body
            self._until = now + self._ttl_s
        return body


def ask_rate_limits(argv: tuple[str, ...], *, timeout_s: float = _ASK_TIMEOUT_S) -> bytes:
    """Start `codex app-server`, ask once, return that reply line. Empty on any failure."""
    try:
        process = subprocess.Popen(  # noqa: S603 -- argv is a list; the binary path is absolute
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return b""
    try:
        return _exchange(process, timeout_s)
    finally:
        _stop(process)


def _exchange(process: subprocess.Popen[bytes], timeout_s: float) -> bytes:
    stdin, stdout = process.stdin, process.stdout
    if stdin is None or stdout is None:
        return b""
    try:
        _ = stdin.write(_ASK)
        stdin.flush()
    except OSError:
        return b""
    deadline = time.monotonic() + timeout_s
    pending = b""
    descriptor = stdout.fileno()
    while (left := deadline - time.monotonic()) > 0:
        readable, _, _ = select.select([descriptor], [], [], left)
        if not readable:
            return b""
        chunk = os.read(descriptor, _READ_BYTES)
        if chunk == b"":
            return b""
        pending += chunk
        *lines, pending = pending.split(b"\n")
        for line in lines:
            if _is_answer(line):
                return line
    return b""


def _is_answer(line: bytes) -> bool:
    try:
        return msgspec.json.decode(line, type=_Reply).id == _RATE_LIMITS_ID
    except msgspec.DecodeError:
        return False


def _stop(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            with contextlib.suppress(OSError):
                stream.close()
    try:
        os.killpg(process.pid, signal.SIGTERM)
        _ = process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        process.kill()
        _ = process.wait()
