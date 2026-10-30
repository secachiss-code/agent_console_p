"""`Settings.setup_completed` closes the wizard and stays closed across a restart."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import msgspec

if TYPE_CHECKING:
    from pathlib import Path

from aconsole.contracts.app import AppSnapshot, EmptyParams, SettingsUpdateParams, SnapshotResult
from aconsole.contracts.common import ClientRole
from aconsole.contracts.ipc import InitializeParams, ProtocolVersion, Request, ResponseErr, ResponseOk
from aconsole.contracts.settings import Settings, SettingsPatch
from aconsole.core.bootstrap import Daemon
from aconsole.core.infrastructure.ipc.framing import decode_envelope, encode_line
from tests.core.conftest import wait_for_socket

_ENCODER = msgspec.json.Encoder()
_WAIT_S = 15.0


def test_setup_completed_survives_restart_and_a_failed_check(tmp_path: Path) -> None:
    """An update closes the wizard, a repeat keeps rev, a failed check does not reopen it, and a restart agrees."""
    required, rev, again, failing, loaded = asyncio.run(_finish(tmp_path))
    assert required == (True, False, False, False)
    assert rev == 1
    assert again == 1
    assert failing == (2, 1)
    assert loaded.setup_completed is True
    assert loaded.rev == 1


async def _finish(tmp_path: Path) -> tuple[tuple[bool, bool, bool, bool], int, int, tuple[int, int], Settings]:
    database = tmp_path / "aconsole.db"
    sock = tmp_path / "core.sock"
    daemon = Daemon(sock, idle_s=30, database=database)
    task = asyncio.create_task(daemon.serve())
    await wait_for_socket(sock)
    try:
        reader, writer = await asyncio.open_unix_connection(sock)
        try:
            await _init(reader, writer)
            before = _snapshot(await _send(reader, writer, 2, "app/snapshot", EmptyParams()))
            updated = _settings(
                await _send(
                    reader,
                    writer,
                    3,
                    "settings/update",
                    SettingsUpdateParams(patch=SettingsPatch(setup_completed=True), rev=before.settings_rev),
                )
            )
            closed = _snapshot(await _send(reader, writer, 4, "app/snapshot", EmptyParams()))
            repeated = _settings(
                await _send(
                    reader,
                    writer,
                    5,
                    "settings/update",
                    SettingsUpdateParams(patch=SettingsPatch(setup_completed=True), rev=updated.rev),
                )
            )
            daemon.tabs.note_setup_checks(2, 1)
            checked = daemon.tabs.snapshot()
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        daemon.request_stop()
        _ = await asyncio.wait_for(task, _WAIT_S)
    daemon = Daemon(sock, idle_s=30, database=database)
    task = asyncio.create_task(daemon.serve())
    await wait_for_socket(sock)
    try:
        reader, writer = await asyncio.open_unix_connection(sock)
        try:
            await _init(reader, writer)
            restored = _snapshot(await _send(reader, writer, 2, "app/snapshot", EmptyParams()))
            loaded = _settings(await _send(reader, writer, 3, "settings/get", EmptyParams()))
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        daemon.request_stop()
        _ = await asyncio.wait_for(task, _WAIT_S)
    flags = (before.setup.required, closed.setup.required, checked.setup.required, restored.setup.required)
    return flags, updated.rev, repeated.rev, (checked.setup.failing_checks, checked.setup.warnings), loaded


def _snapshot(found: ResponseOk) -> AppSnapshot:
    body = msgspec.json.decode(found.result, type=SnapshotResult)
    return body.snapshot


def _settings(found: ResponseOk) -> Settings:
    return msgspec.json.decode(found.result, type=Settings)


async def _init(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    params = InitializeParams(
        protocol=(ProtocolVersion(major=1, minor=0), ProtocolVersion(major=1, minor=0)),
        role=ClientRole.UI,
        client_name="test",
        client_version="0.0.0",
        pid=1,
        capabilities=frozenset({"events.v1"}),
    )
    _ = await _send(reader, writer, 1, "initialize", params)


async def _send(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request_id: int,
    method: str,
    params: msgspec.Struct,
) -> ResponseOk:
    payload = Request(id=request_id, method=method, params=msgspec.Raw(_ENCODER.encode(params)))
    writer.write(encode_line(_ENCODER, payload))
    await writer.drain()
    frame = decode_envelope(await asyncio.wait_for(reader.readuntil(b"\n"), _WAIT_S))
    if not isinstance(frame, ResponseOk):
        detail = frame.error.message if isinstance(frame, ResponseErr) else "empty frame"
        message = f"{method} failed: {detail}"
        raise TypeError(message)
    return frame
