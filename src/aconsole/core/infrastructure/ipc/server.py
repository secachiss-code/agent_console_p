"""Unix socket for the core. The directory is 0700 and the socket is 0600."""

from __future__ import annotations

import asyncio
import os
import socket
import struct
from pathlib import Path
from typing import final

import msgspec

from aconsole.contracts.errors import ErrorData, ErrorObject, MessageTooLargeDetails, error_by_name
from aconsole.contracts.ipc import ResponseErr
from aconsole.core.application.services.tabs import TabService
from aconsole.core.infrastructure.ipc.framing import MAX_MESSAGE_BYTES, MessageTooLargeError, encode_line, read_frame
from aconsole.core.infrastructure.ipc.ring import EventRing
from aconsole.core.infrastructure.ipc.session import Connection


def peer_uid(sock: object) -> int:
    """Uid from ``SO_PEERCRED``. A different uid is not this user."""
    getsockopt = getattr(sock, "getsockopt", None)
    if not callable(getsockopt):
        message = "socket has no credentials"
        raise TypeError(message)
    raw = getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    if not isinstance(raw, (bytes, bytearray)):
        message = "credentials were not bytes"
        raise TypeError(message)
    _pid, uid, _gid = struct.unpack("3i", bytes(raw))
    return uid


def allow_peer(uid: int) -> bool:
    return uid == os.getuid()


def _peer_uid(writer: asyncio.StreamWriter) -> int:
    try:
        # asyncio types the socket as Any.
        socket_info = writer.get_extra_info("socket")  # pyright: ignore[reportAny] -- asyncio types this as Any
        return peer_uid(socket_info)  # pyright: ignore[reportAny] -- asyncio types this as Any
    except AttributeError, OSError, TypeError, struct.error:
        return -1


@final
class IpcServer:
    """Accept local clients and run one ``Connection`` each."""

    def __init__(self, path: Path, ring: EventRing | None = None, *, tabs: TabService) -> None:
        self.path = path
        self.ring = ring or EventRing()
        self.peers: list[int] = []
        self.tabs = tabs
        self._server: asyncio.AbstractServer | None = None
        self._encoder = msgspec.json.Encoder()

    async def start(self) -> None:
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        if self.path.exists():
            self.path.unlink()
        self._server = await asyncio.start_unix_server(
            self._client,
            self.path,
            limit=MAX_MESSAGE_BYTES + 1,
        )
        self.path.chmod(0o600)

    async def stop(self) -> None:
        server = self._server
        self._server = None
        if server is None:
            return
        server.close()
        await server.wait_closed()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        uid = _peer_uid(writer)
        self.peers.append(uid)
        if not allow_peer(uid):
            writer.close()
            await writer.wait_closed()
            return
        connection = Connection(self.ring, tabs=self.tabs)
        try:
            while True:
                try:
                    frame = await read_frame(reader)
                except MessageTooLargeError:
                    writer.write(_too_large(self._encoder))
                    await writer.drain()
                    break
                if frame is None:
                    break
                for payload in connection.receive(frame):
                    writer.write(payload)
                    await writer.drain()
                connection.drained()
        finally:
            writer.close()
            await writer.wait_closed()


def _too_large(encoder: msgspec.json.Encoder) -> bytes:
    spec = error_by_name("MESSAGE_TOO_LARGE")
    error = ErrorObject(
        code=spec.code,
        message=spec.name,
        data=ErrorData(
            reason=spec.name,
            domain="aconsole.ipc",
            retryable=spec.retryable,
            user_message_key="error.message_too_large",
            details=MessageTooLargeDetails(limit=MAX_MESSAGE_BYTES),
        ),
    )
    return encode_line(encoder, ResponseErr(id=None, error=error))
