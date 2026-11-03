"""The client reads what the protocol allows, and recovers from what it cannot read or a snapshot that fails."""

import asyncio
import tempfile

import msgspec
import pytest

from aconsole.contracts.app import EmptyParams, SnapshotResult, SubscribeResult
from aconsole.contracts.ipc import ProtocolLimits, Request
from aconsole.core.infrastructure.fake.server import FakeCore
from aconsole.ui.client.core_client import CoreClient, CoreTimeoutError

_LIMIT = ProtocolLimits().max_message_bytes


class _Pad(msgspec.Struct, frozen=True):
    pad: str


def _reply(request_id: int, size: int) -> bytes:
    head = b'{"kind":"result","id":%d,"result":{"pad":"' % request_id
    tail = b'"}}\n'
    return head + b"x" * (size - len(head) - len(tail)) + tail


async def _answering(size: int) -> tuple[asyncio.Server, str]:
    async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = await reader.readline()
        if line:
            writer.write(_reply(msgspec.json.decode(line, type=Request).id, size))
            await writer.drain()
        await reader.read()
        writer.close()

    path = f"{tempfile.mkdtemp(prefix='aconsole-gui-')}/core.sock"
    return await asyncio.start_unix_server(client, path), path


async def _read_of(size: int) -> int:
    server, path = await _answering(size)
    client = CoreClient(path, timeout_s=20)
    try:
        await client._open_socket()
        return len((await client.request("app/snapshot", EmptyParams(), _Pad)).pad)
    finally:
        await client.close()
        server.close()


@pytest.mark.parametrize("size", [70 * 1024, 4 * 1024 * 1024, _LIMIT])
def test_a_reply_within_the_limit_arrives_whole(size: int) -> None:
    assert asyncio.run(_read_of(size)) > size - 100


async def _over_the_limit() -> bool:
    server, path = await _answering(_LIMIT + 1024)
    client = CoreClient(path, timeout_s=20)
    try:
        await client._open_socket()
        with pytest.raises(ConnectionError):
            _ = await client.request("app/snapshot", EmptyParams(), _Pad)
        await asyncio.sleep(0)
        return client.store.reconnecting
    finally:
        await client.close()
        server.close()


def test_a_reply_over_the_limit_fails_the_call_and_reconnects() -> None:
    assert asyncio.run(_over_the_limit())


class _FirstSnapshotFails(CoreClient):
    failures: int = 0

    async def pull_snapshot(self) -> tuple[SnapshotResult, SubscribeResult]:
        if self.failures > 0:
            self.failures -= 1
            method = "app/snapshot"
            raise CoreTimeoutError(method)
        return await super().pull_snapshot()


async def _recovers(path: str) -> tuple[int, bool, int]:
    client = _FirstSnapshotFails(path, timeout_s=1)
    try:
        _ = await client.connect()
        client.failures = 1
        await client._resync()
        return client.resyncs, client.store.reconnecting, client.failures
    finally:
        await client.close()


def test_a_failed_resync_tries_again_and_restores_the_ui() -> None:
    directory = tempfile.mkdtemp(prefix="aconsole-gui-")
    core = FakeCore(f"{directory}/core.sock", "demo")
    core.start()
    try:
        resyncs, reconnecting, failures = asyncio.run(_recovers(core.path))
    finally:
        core.stop()
    assert (resyncs, reconnecting, failures) == (0, False, 0)


def test_restoring_a_checkpoint_gets_the_long_timeout() -> None:
    client = CoreClient("unused", timeout_s=0.01)
    assert client.timeout_for("checkpoint/restore") == 60
    assert client.timeout_for("tab/restart") == 60
    assert client.timeout_for("app/snapshot") == 0.01
