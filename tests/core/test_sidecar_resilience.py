"""Backpressure and a closed terminal must not lose lifecycle notifications."""

from __future__ import annotations

import os
import signal
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest

from aconsole.sidecar import main as sidecar
from aconsole.sidecar.ipc import exited

if TYPE_CHECKING:
    from pathlib import Path


class _Socket:
    def __init__(self) -> None:
        self.data = bytearray()
        self.blocked = False
        self.closed = False
        self.partial = False

    def settimeout(self, _timeout: float) -> None:
        pass

    def connect(self, _path: str) -> None:
        pass

    def sendall(self, data: bytes) -> None:
        if self.blocked:
            raise TimeoutError
        self.data.extend(data)

    def send(self, data: bytes) -> int:
        if self.blocked:
            raise BlockingIOError
        size = 7 if self.partial else len(data)
        self.data.extend(data[:size])
        if self.partial:
            self.blocked = True
        return min(size, len(data))

    def close(self) -> None:
        self.closed = True


def test_a_slow_core_keeps_the_link_and_partial_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = _Socket()
    monkeypatch.setattr(socket, "socket", lambda *_args: sock)
    link = sidecar.CoreLink("/core.sock", "tab-a", "proof")
    initial = bytes(sock.data)
    sock.blocked = True
    payload = exited(0, None)
    link.send(payload)
    assert not sock.closed, "temporary backpressure is not a broken connection"
    sock.blocked = False
    sock.partial = True
    link.flush()
    sock.partial = False
    sock.blocked = False
    link.flush()
    link.close()
    assert bytes(sock.data) == initial + payload


class _Link:
    def __init__(self, *_args: object) -> None:
        self.messages: list[bytes] = []
        self.closed = False

    def send(self, data: bytes) -> None:
        self.messages.append(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("during_cleanup", [False, True])
def test_closed_stdout_reports_exit_and_reaps_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, during_cleanup: bool,
) -> None:
    link = _Link()
    monkeypatch.setattr(sidecar, "CoreLink", lambda *_args: link)
    monkeypatch.setattr(sidecar, "_stdin_fd", lambda: None)
    pid_file = tmp_path / "child.pid"

    def fail(_data: bytes) -> None:
        raise BrokenPipeError

    monkeypatch.setattr(sidecar, "_write_stdout", fail)
    if during_cleanup:
        monkeypatch.setattr(sidecar, "_drain_fd", lambda *_args: b"last output")
        command = ["/bin/true"]
    else:
        command = ["/bin/sh", "-c", 'trap \'\' HUP; echo $$ > "$1"; printf output; sleep 30', "sh", str(pid_file)]
    error = None
    try:
        try:
            sidecar.main(["--", *command], environ={"PATH": "/usr/bin:/bin"})
        except OSError as exc:
            error = exc
        assert any(b"sidecar/exited" in message for message in link.messages)
        assert link.closed
        assert error is None
        expected = exited(0, None) if during_cleanup else exited(None, signal.SIGKILL)
        assert link.messages[-1] == expected
        if pid_file.exists():
            with pytest.raises(ChildProcessError):
                os.waitpid(int(pid_file.read_text()), os.WNOHANG)
    finally:
        if pid_file.exists():
            pid = int(pid_file.read_text())
            try:
                os.killpg(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass


def test_dead_peer_does_not_hold_the_terminal(tmp_path: Path) -> None:
    path = str(tmp_path / "core.sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(path)
        server.listen(1)
        link = sidecar.CoreLink(path, "tab-a", "proof")
        with server.accept()[0] as peer:
            _ = peer.recv(65536)
        start = time.monotonic()
        link.send(exited(0, None))
        link.close()
        assert time.monotonic() - start < 1


def test_real_socket_recovers_after_more_than_the_old_timeout(tmp_path: Path) -> None:
    from aconsole.sidecar.ipc import signal as encode_signal

    path = str(tmp_path / "slow.sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(path)
        server.listen(1)
        link = sidecar.CoreLink(path, "tab-a", "proof")
        assert link._sock is not None
        link._sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        with server.accept()[0] as peer:
            peer.settimeout(2)
            initial = peer.recv(65536)
            payload = encode_signal("osc_title", "x" * 1024, None)
            copies = 100
            for _ in range(copies):
                link.send(payload)
            assert link._sock is not None
            assert link._pending

            def receive() -> bytes:
                time.sleep(0.15)
                out = bytearray()
                while chunk := peer.recv(65536):
                    out.extend(chunk)
                return bytes(out)

            with ThreadPoolExecutor(max_workers=1) as pool:
                received = pool.submit(receive)
                link.send(exited(0, None))
                link.close()
                assert received.result(timeout=2) == payload * copies + exited(0, None)
            assert b'"method":"initialize"' in initial


def test_stalled_core_has_bounded_queue_and_shutdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = str(tmp_path / "stalled.sock")
    monkeypatch.setattr(sidecar, "_LINK_DRAIN_S", 0.01)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(path)
        server.listen(1)
        link = sidecar.CoreLink(path, "tab-a", "proof")
        assert link._sock is not None
        link._sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        with server.accept()[0]:
            link.send(b"x" * (sidecar._LINK_PENDING_BYTES // 2))
            assert link._pending
            start = time.monotonic()
            link.close()
            assert time.monotonic() - start < 1
            assert not link._pending
            assert link._sock is None
        overflow = sidecar.CoreLink(path, "tab-b", "proof")
        overflow.send(b"x" * (sidecar._LINK_PENDING_BYTES + 1))
        assert overflow._sock is None
        assert not overflow._pending
