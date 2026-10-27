"""Daemon lock, idle shutdown, and the `--serve` entry."""

from __future__ import annotations

import asyncio
import os
import secrets
import stat
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import msgspec
import pytest

from aconsole.contracts.app import OpenTabParams, TabIdParams
from aconsole.contracts.common import AgentKind, ClientRole, ProjectId, TabId, WorkspaceMode
from aconsole.contracts.ipc import Envelope, InitializeParams, ProtocolVersion, Request, ResponseErr, ResponseOk
from aconsole.core.__main__ import main
from aconsole.core.bootstrap import (
    IDLE_EXIT_S,
    Daemon,
    credentials_uid,
    extension_modules,
    gil_refusal,
    initialize_role,
    parse_serve,
)
from aconsole.core.infrastructure.ipc.framing import decode_envelope, encode_line
from tests.core.conftest import use_fake_tabs, wait_for_socket

if TYPE_CHECKING:
    from pathlib import Path

_ENCODER = msgspec.json.Encoder()
_LIMIT = 32


def _line(value: msgspec.Struct) -> bytes:
    return encode_line(_ENCODER, value)


def _init(role: ClientRole, *, tab_id: TabId | None = None, token: str | None = None) -> bytes:
    params = InitializeParams(
        protocol=(ProtocolVersion(major=1, minor=0), ProtocolVersion(major=1, minor=0)),
        role=role,
        client_name="test",
        client_version="0.0.0",
        pid=os.getpid(),
        capabilities=frozenset({"events.v1"}),
        tab_id=tab_id,
        sidecar_token=token,
    )
    return _line(Request(id=1, method="initialize", params=msgspec.Raw(_ENCODER.encode(params))))


async def _open(path: Path, role: ClientRole) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_unix_connection(path)
    writer.write(_init(role))
    await writer.drain()
    frame = decode_envelope(await reader.readuntil(b"\n"))
    if not isinstance(frame, ResponseOk):
        message = "initialize failed"
        raise TypeError(message)
    return reader, writer


def test_ipc_b_idle_constant() -> None:
    assert IDLE_EXIT_S == 15


def test_ipc_b_credentials_and_role() -> None:
    assert credentials_uid(object()) == -1
    assert isinstance(extension_modules(), tuple)
    assert initialize_role(b"not-json\n") is None
    assert initialize_role(_init(ClientRole.UI)) is ClientRole.UI
    assert initialize_role(_init(ClientRole.SIDECAR)) is ClientRole.SIDECAR


def test_ipc_b_serve_argv(tmp_path: Path) -> None:
    assert parse_serve([]) is None
    assert parse_serve(["--serve", "--socket"]) is None
    assert parse_serve(["--serve", "--socket", "--allow-gil"]) is None
    assert parse_serve(["--serve", "--extra"]) is None
    socket = tmp_path / "core.sock"
    assert parse_serve(["--serve", "--socket", str(socket), "--allow-gil"]) == (socket, True)
    assert parse_serve(["--serve", "--allow-gil", "--socket", str(socket)]) == (socket, True)
    bare = parse_serve(["--serve"])
    assert bare is not None
    assert bare[0].name == "core.sock"
    assert bare[1] is False


def test_ipc_b_gil_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert gil_refusal(allow_gil=False, enabled=False, candidates=()) is None
    assert gil_refusal(allow_gil=True, enabled=True, candidates=("gi",)) is None
    text = gil_refusal(allow_gil=False, enabled=True, candidates=("mod_a", "mod_b"))
    assert text is not None
    assert "mod_a" in text
    assert "mod_b" in text
    empty = gil_refusal(allow_gil=False, enabled=True, candidates=())
    assert empty is not None
    assert "(none)" in empty
    monkeypatch.setattr(sys, "_is_gil_enabled", lambda: True)
    code = main(["--serve", "--socket", str(tmp_path / "core.sock")])
    assert code == 1
    assert "GIL is enabled" in capsys.readouterr().err
    assert not (tmp_path / "core.sock").exists()


def test_ipc_b_usage() -> None:
    assert main([]) == 2
    assert main(["--serve", "--nope"]) == 2


def test_ipc_b_negative_tabs(tmp_path: Path) -> None:
    daemon = Daemon(tmp_path / "core.sock")
    with pytest.raises(ValueError, match="negative"):
        daemon.set_live_tabs(-1)
    with pytest.raises(ValueError, match="negative"):
        _ = Daemon(tmp_path / "core.sock", idle_s=-1)
    with pytest.raises(ValueError, match="positive"):
        _ = Daemon(tmp_path / "core.sock", frame_limit=0)
    assert daemon.lock_path == tmp_path / "core.lock"


def test_ipc_b_exits_when_idle(tmp_path: Path) -> None:
    sock = tmp_path / "aconsole" / "core.sock"
    assert asyncio.run(Daemon(sock, idle_s=0.05).serve()) == 0
    assert not sock.exists()
    assert asyncio.run(Daemon(sock, idle_s=0.05).serve()) == 0


def test_ipc_b_socket_mode(tmp_path: Path) -> None:
    mode, directory, lock_mode = asyncio.run(_modes(tmp_path))
    assert mode == 0o600
    assert directory == 0o700
    assert lock_mode == 0o600


def test_ipc_b_ui_and_cli_hold(tmp_path: Path) -> None:
    assert asyncio.run(_holds(tmp_path)) == 0


def test_ipc_b_sidecar_does_not_hold(tmp_path: Path) -> None:
    assert asyncio.run(_sidecar(tmp_path)) == 0


def test_ipc_b_live_tabs_hold(tmp_path: Path) -> None:
    assert asyncio.run(_tabs(tmp_path)) == 0


def _no_credentials(_sock: object) -> int:
    return -1


def test_ipc_b_rejects_other_uid_and_oversize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aconsole.core.bootstrap.credentials_uid", _no_credentials)
    assert asyncio.run(_reject(tmp_path)) == 0


def test_ipc_b_oversize_frame(tmp_path: Path) -> None:
    assert asyncio.run(_oversize(tmp_path)) == "MESSAGE_TOO_LARGE"


def test_ipc_b_bad_initialize_does_not_hold(tmp_path: Path) -> None:
    assert asyncio.run(_bad_init(tmp_path)) == 0


def test_ipc_b_second_process_leaves_the_socket(tmp_path: Path) -> None:
    sock = tmp_path / "aconsole" / "core.sock"
    first = subprocess.Popen(
        [sys.executable, "-m", "aconsole.core", "--serve", "--allow-gil", "--socket", str(sock)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not sock.exists():
            if first.poll() is not None or time.monotonic() > deadline:
                if first.poll() is None:
                    first.kill()
                _out, err = first.communicate(timeout=2)
                message = err or "daemon exited before the socket appeared"
                raise AssertionError(message)
            time.sleep(0.02)
        second = subprocess.run(
            [sys.executable, "-m", "aconsole.core", "--serve", "--allow-gil", "--socket", str(sock)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert second.returncode == 1
        assert "core lock is held" in second.stderr
        assert sock.exists()
        frame = asyncio.run(_ping(sock))
        assert isinstance(frame, ResponseOk)
    finally:
        if first.poll() is None:
            first.terminate()
        try:
            _ = first.wait(timeout=3)
        except subprocess.TimeoutExpired:
            first.kill()
            _ = first.wait(timeout=3)


async def _modes(tmp_path: Path) -> tuple[int, int, int]:
    sock = tmp_path / "aconsole" / "core.sock"
    daemon = Daemon(sock, idle_s=30)
    task = asyncio.create_task(daemon.serve())
    await wait_for_socket(sock)
    mode = stat.S_IMODE(sock.stat().st_mode)
    directory = stat.S_IMODE(sock.parent.stat().st_mode)
    lock_mode = stat.S_IMODE(daemon.lock_path.stat().st_mode)
    daemon.request_stop()
    assert await asyncio.wait_for(task, 2) == 0
    return mode, directory, lock_mode


async def _holds(tmp_path: Path) -> int:
    sock = tmp_path / "core.sock"
    daemon = Daemon(sock, idle_s=0.4)
    task = asyncio.create_task(daemon.serve())
    await wait_for_socket(sock)
    _ui_reader, ui = await _open(sock, ClientRole.UI)
    _cli_reader, cli = await _open(sock, ClientRole.CLI)
    await asyncio.sleep(0.7)
    assert not task.done()
    ui.close()
    await ui.wait_closed()
    await asyncio.sleep(0.7)
    assert not task.done()
    cli.close()
    await cli.wait_closed()
    return await asyncio.wait_for(task, 2)


async def _sidecar(tmp_path: Path) -> int:
    """A sidecar needs the one-shot token of `tab/launchSpec`. It does not count as a holder."""
    sock = tmp_path / "core.sock"
    daemon = Daemon(sock, idle_s=0.25)
    _ = use_fake_tabs(daemon, tmp_path)
    opened = daemon.tabs.open_tab(
        OpenTabParams(
            project_id=ProjectId("proj"),
            agent=AgentKind.CLAUDE,
            workspace=WorkspaceMode.MAIN_CHECKOUT,
            title="chat",
        ),
    )
    spec = daemon.tabs.launch_spec(TabIdParams(tab_id=opened.id))
    token = spec.env["ACONSOLE_SIDECAR_TOKEN"]
    task = asyncio.create_task(daemon.serve())
    await wait_for_socket(sock)
    refused, _refused_reader, refused_writer = await _handshake(
        sock,
        _init(ClientRole.SIDECAR, tab_id=opened.id, token=secrets.token_hex(32)),
    )
    refused_writer.close()
    assert isinstance(refused, ResponseErr)
    assert refused.error.message == "SIDECAR_TOKEN_INVALID"
    accepted, _reader, writer = await _handshake(sock, _init(ClientRole.SIDECAR, tab_id=opened.id, token=token))
    try:
        assert isinstance(accepted, ResponseOk)
        return await asyncio.wait_for(task, 2)
    finally:
        writer.close()
        await writer.wait_closed()


async def _handshake(
    path: Path,
    frame: bytes,
) -> tuple[Envelope, asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_unix_connection(path)
    writer.write(frame)
    await writer.drain()
    envelope = decode_envelope(await asyncio.wait_for(reader.readuntil(b"\n"), 2))
    return envelope, reader, writer


async def _tabs(tmp_path: Path) -> int:
    sock = tmp_path / "core.sock"
    daemon = Daemon(sock, idle_s=0.25, live_tabs=1)
    task = asyncio.create_task(daemon.serve())
    await wait_for_socket(sock)
    await asyncio.sleep(0.5)
    assert not task.done()
    daemon.set_live_tabs(0)
    return await asyncio.wait_for(task, 2)


async def _reject(tmp_path: Path) -> int:
    sock = tmp_path / "core.sock"
    daemon = Daemon(sock, idle_s=30)
    task = asyncio.create_task(daemon.serve())
    await wait_for_socket(sock)
    reader, writer = await asyncio.open_unix_connection(sock)
    data = await asyncio.wait_for(reader.read(16), 1)
    assert data == b""
    writer.close()
    daemon.request_stop()
    return await asyncio.wait_for(task, 2)


async def _oversize(tmp_path: Path) -> str:
    sock = tmp_path / "core.sock"
    daemon = Daemon(sock, idle_s=30, frame_limit=_LIMIT)
    task = asyncio.create_task(daemon.serve())
    await wait_for_socket(sock)
    reader, writer = await asyncio.open_unix_connection(sock)
    writer.write(b"x" * 40 + b"\n")
    await writer.drain()
    frame = decode_envelope(await asyncio.wait_for(reader.readuntil(b"\n"), 1))
    assert isinstance(frame, ResponseErr)
    message = frame.error.message
    writer.close()
    daemon.request_stop()
    assert await asyncio.wait_for(task, 2) == 0
    return message


async def _bad_init(tmp_path: Path) -> int:
    sock = tmp_path / "core.sock"
    daemon = Daemon(sock, idle_s=0.25)
    task = asyncio.create_task(daemon.serve())
    await wait_for_socket(sock)
    reader, writer = await asyncio.open_unix_connection(sock)
    writer.write(_line(Request(id=1, method="initialize", params=msgspec.Raw(b"{}"))))
    await writer.drain()
    _ = await asyncio.wait_for(reader.read(32), 1)
    writer.close()
    return await asyncio.wait_for(task, 2)


async def _ping(path: Path) -> ResponseOk:
    reader, writer = await asyncio.open_unix_connection(path)
    writer.write(_init(ClientRole.CLI))
    await writer.drain()
    decoded = decode_envelope(await reader.readuntil(b"\n"))
    writer.close()
    await writer.wait_closed()
    if not isinstance(decoded, ResponseOk):
        message = "initialize failed"
        raise TypeError(message)
    return decoded
