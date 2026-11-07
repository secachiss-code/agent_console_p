"""IPC server: socket, framing, handshake, cancel, inflight, ring, backpressure."""

from __future__ import annotations

import asyncio
import logging
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import msgspec
import pytest
from hypothesis import given
from hypothesis import strategies as st

from aconsole.contracts.app import SubscribeParams
from aconsole.contracts.common import ClientRole, TabId
from aconsole.contracts.events import Notify
from aconsole.contracts.ipc import (
    CancelParams,
    InitializeParams,
    Notification,
    ProtocolVersion,
    Request,
    ResponseErr,
    ResponseOk,
)
from aconsole.core.infrastructure.ipc.framing import (
    MAX_MESSAGE_BYTES,
    MessageTooLargeError,
    decode_envelope,
    encode_line,
    read_frame,
)
from aconsole.core.infrastructure.ipc.ring import EventRing
from aconsole.core.infrastructure.ipc.server import IpcServer, allow_peer, note_peer, same_mnt_namespace
from aconsole.core.infrastructure.ipc.session import OUTBOUND_LIMIT, Connection
from aconsole.core.infrastructure.tabs import default_tabs

_WHEN = datetime(2026, 10, 26, tzinfo=UTC)
_ENCODER = msgspec.json.Encoder()


def _line(value: msgspec.Struct) -> bytes:
    return encode_line(_ENCODER, value)


_CAPS: frozenset[str] = frozenset({"events.v1"})


def _init(*versions: tuple[int, int], caps: frozenset[str] = _CAPS, role: ClientRole = ClientRole.UI) -> bytes:
    low, high = versions or ((1, 0), (1, 0))
    params = InitializeParams(
        protocol=(ProtocolVersion(major=low[0], minor=low[1]), ProtocolVersion(major=high[0], minor=high[1])),
        role=role,
        client_name="test",
        client_version="0.0.0",
        pid=os.getpid(),
        capabilities=caps,
    )
    return _line(Request(id=1, method="initialize", params=msgspec.Raw(_ENCODER.encode(params))))


def _subscribe(request_id: int, after_seq: int) -> bytes:
    params = SubscribeParams(after_seq=after_seq)
    return _line(Request(id=request_id, method="events/subscribe", params=msgspec.Raw(_ENCODER.encode(params))))


def _hold(request_id: int) -> bytes:
    return _line(Request(id=request_id, method="core/hold", params=msgspec.Raw(b"{}")))


def test_ipc_a_limit_constant() -> None:
    assert MAX_MESSAGE_BYTES == 16 * 1024 * 1024
    assert OUTBOUND_LIMIT == 1024


def test_ipc_a_frame_limit() -> None:
    assert asyncio.run(_oversize())


async def _oversize() -> bool:
    reader = asyncio.StreamReader(limit=128)
    reader.feed_data(b"x" * 40 + b"\n")
    try:
        _ = await read_frame(reader, limit=16)
    except MessageTooLargeError:
        return True
    return False


def test_ipc_peer_history_keeps_the_newest_entries() -> None:
    peers: list[int] = []
    for uid in range(40):
        note_peer(peers, uid)
    assert peers == list(range(8, 40))


def test_ipc_a_other_uid_is_rejected() -> None:
    assert allow_peer(os.getuid())
    assert not allow_peer(os.getuid() + 1)


def test_ipc_a_socket_mode_and_peer(tmp_path: Path) -> None:
    mode, directory, peers = asyncio.run(_socket(tmp_path))
    assert mode == 0o600
    assert directory == 0o700
    assert peers == [os.getuid()]


def test_ipc_a_version_mismatch() -> None:
    connection = Connection(EventRing())
    frames = connection.receive(_init((2, 0), (2, 0)))
    error = decode_envelope(frames[0])
    assert isinstance(error, ResponseErr)
    assert error.error.message == "PROTOCOL_VERSION_UNSUPPORTED"
    assert not connection.ready


def test_ipc_a_cancel() -> None:
    connection = Connection(EventRing(), host_namespace=True)
    _ = connection.receive(_init())
    assert connection.receive(_hold(2)) == ()
    note = Notification(method="$/cancelRequest", params=msgspec.Raw(_ENCODER.encode(CancelParams(id=2))))
    frames = connection.receive(_line(note))
    error = decode_envelope(frames[0])
    assert isinstance(error, ResponseErr)
    assert error.error.message == "REQUEST_CANCELLED"
    assert 2 not in connection.inflight


def test_ipc_a_max_inflight() -> None:
    connection = Connection(EventRing(), host_namespace=True)
    _ = connection.receive(_init())
    for request_id in range(2, 66):
        assert connection.receive(_hold(request_id)) == ()
    busy = decode_envelope(connection.receive(_hold(66))[0])
    assert isinstance(busy, ResponseErr)
    assert busy.error.message == "SERVER_BUSY"


def test_ipc_a_ring_resync() -> None:
    ring = EventRing(size=4)
    for index in range(6):
        _ = ring.append(_notice(str(index)), _WHEN)
    assert ring.since(0) is None
    replay = ring.since(3)
    assert replay is not None
    assert [item.seq for item in replay] == [4, 5, 6]
    assert ring.since(1) is None


def test_ipc_a_backpressure() -> None:
    connection = Connection(EventRing(), outbound_limit=2, host_namespace=True)
    ready = decode_envelope(connection.receive(_init())[0])
    assert isinstance(ready, ResponseOk)
    # IPC §6: nothing is pushed live before `events/subscribe`; the early event stays in the ring.
    assert connection.push_event(_notice("early"), _WHEN) == ()
    subscribed = decode_envelope(connection.receive(_subscribe(2, connection.ring.current_seq))[0])
    assert isinstance(subscribed, ResponseOk)
    assert len(connection.push_event(_notice("a"), _WHEN)) == 1
    assert len(connection.push_event(_notice("b"), _WHEN)) == 1
    stalled = connection.push_event(_notice("c"), _WHEN)
    assert len(stalled) == 1
    event = decode_envelope(stalled[0])
    assert isinstance(event, Notification)
    assert b"backpressure" in bytes(event.params)
    assert connection.push_event(_notice("d"), _WHEN) == ()


@given(st.binary(max_size=128))
def test_ipc_a_envelope_fuzz(payload: bytes) -> None:
    try:
        found = decode_envelope(payload)
    except msgspec.DecodeError:
        return
    assert isinstance(found, (Request, Notification, ResponseOk, ResponseErr))


def test_this_process_shares_its_mount_namespace_and_another_inode_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert same_mnt_namespace(os.getpid())
    assert not same_mnt_namespace(0)
    assert not same_mnt_namespace(1 << 30)
    real_stat = Path.stat

    def other_namespace(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if str(self) == "/proc/self/ns/mnt":
            return real_stat(self, follow_symlinks=follow_symlinks)
        return real_stat(Path("/proc/self/ns/pid"), follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", other_namespace)
    assert not same_mnt_namespace(os.getpid())


def test_outside_the_core_namespace_cannot_take_a_host_role(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    connection = Connection(EventRing())
    refused = decode_envelope(connection.receive(_init())[0])
    assert isinstance(refused, ResponseErr)
    assert refused.error.message == "ROLE_FORBIDDEN"
    assert connection.role is None
    assert any("refused ui" in record.message for record in caplog.records)
    cli = decode_envelope(connection.receive(_init(role=ClientRole.CLI))[0])
    assert isinstance(cli, ResponseErr)
    assert cli.error.message == "ROLE_FORBIDDEN"
    for request_id, method in ((2, "settings/update"), (3, "approval/decide"), (4, "events/subscribe")):
        frame = connection.receive(_line(Request(id=request_id, method=method, params=msgspec.Raw(b"{}"))))
        blocked = decode_envelope(frame[0])
        assert isinstance(blocked, ResponseErr)
        assert blocked.error.message == "NOT_INITIALIZED"


@pytest.mark.parametrize("peer_pid", [None, 4242])
def test_sidecar_outside_the_namespace_keeps_its_token_and_cannot_decide(peer_pid: int | None) -> None:
    proof = "shown-to-the-test"

    class _Tokens:
        def claim_sidecar(self, tab_id: str, offered: str) -> bool:
            return tab_id == "tab-1" and offered == proof

    connection = Connection(EventRing(), tabs=_Tokens(), peer_pid=peer_pid)  # pyright: ignore[reportArgumentType] -- only the token claim is used
    params = InitializeParams(
        protocol=(ProtocolVersion(major=1, minor=0), ProtocolVersion(major=1, minor=0)),
        role=ClientRole.SIDECAR,
        client_name="sidecar",
        client_version="0",
        pid=1,
        capabilities=_CAPS,
        tab_id=TabId("tab-1"),
        sidecar_token=proof,
    )
    body = _line(Request(id=1, method="initialize", params=msgspec.Raw(_ENCODER.encode(params))))
    ready = decode_envelope(connection.receive(body)[0])
    assert isinstance(ready, ResponseOk)
    assert connection.role is ClientRole.SIDECAR
    assert connection.peer_pid == peer_pid, "a container PID cannot replace the kernel peer PID"
    for request_id, method in ((2, "settings/update"), (3, "approval/decide"), (4, "events/subscribe")):
        frame = connection.receive(_line(Request(id=request_id, method=method, params=msgspec.Raw(b"{}"))))
        blocked = decode_envelope(frame[0])
        assert isinstance(blocked, ResponseErr)
        assert blocked.error.message == "ROLE_FORBIDDEN"


def _notice(name: str) -> Notify:
    return Notify(tab_id=None, title=name, body=name, priority="normal", episode_id=name)


async def _socket(path: Path) -> tuple[int, int, list[int]]:
    sock = path / "aconsole" / "core.sock"
    server = IpcServer(sock, tabs=default_tabs(socket_path=str(sock)))
    await server.start()
    try:
        mode = stat.S_IMODE(server.path.stat().st_mode)
        directory = stat.S_IMODE(server.path.parent.stat().st_mode)
        reader, writer = await asyncio.open_unix_connection(server.path)
        writer.write(_init())
        await writer.drain()
        reply = await asyncio.wait_for(reader.readline(), timeout=2)
        peers = list(server.peers)
        writer.close()
        await writer.wait_closed()
        assert reply.startswith(b"{")
    finally:
        await server.stop()
    return mode, directory, peers
