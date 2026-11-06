"""Upgrade refusal uses disposable old-core processes and leaves their agents alive."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from aconsole.contracts.app import EmptyParams
from aconsole.ui.client.core_client import CoreCallError, CoreClient, core_paths, lock_held
from tests.core.conftest import wait_for_socket

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_SERVER = r"""
import asyncio, fcntl, json, os, signal, sys
from pathlib import Path
root = Path(sys.argv[1])
sock = root / 'aconsole/core.sock'
lock = (root / 'aconsole/core.lock').open('w')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

def record(value):
    with (root / 'requests.jsonl').open('a') as stream:
        stream.write(json.dumps(value) + '\n')

signal.signal(signal.SIGTERM, lambda *_args: record('SIGTERM'))

async def accept(reader, writer):
    try:
        while line := await reader.readline():
            request = json.loads(line)
            record(request['method'])
            if request['method'] == 'initialize':
                capabilities = ['events.v1']
                if (root / 'compatible').exists():
                    capabilities.append('work_sessions.v1')
                response = {'jsonrpc': '2.0', 'kind': 'result', 'id': request['id'], 'result': {
                    'protocol': {'major': 1, 'minor': 0}, 'server_version': 'legacy-test',
                    'server_pid': os.getpid(), 'session_id': 'legacy',
                    'capabilities': capabilities, 'limits': {}}}
                writer.write(json.dumps(response).encode() + b'\n')
                await writer.drain()
            elif request['method'] == 'app/snapshot':
                (root / 'compatible').unlink()
                break
    finally:
        record('disconnected')
        writer.close()
        await writer.wait_closed()

async def serve():
    server = await asyncio.start_unix_server(accept, path=str(sock))
    async with server:
        await server.serve_forever()

asyncio.run(serve())
"""


def _events(runtime: Path) -> list[object]:
    path = runtime / "requests.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@asynccontextmanager
async def _old_core() -> AsyncIterator[tuple[Path, asyncio.subprocess.Process, asyncio.subprocess.Process]]:
    with tempfile.TemporaryDirectory(prefix="ac-upgrade-") as directory:
        runtime = Path(directory)
        socket, _lock = core_paths(runtime)
        socket.parent.mkdir()
        (runtime / "agent-data").write_bytes(b"uncommitted agent work\x00\xff")
        agent = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)")
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", _SERVER, str(runtime))
        try:
            await wait_for_socket(socket)
            yield runtime, process, agent
        finally:
            process.kill()  # Only the disposable fixture ignores SIGTERM.
            agent.terminate()
            _ = await process.wait()
            _ = await agent.wait()


async def _legacy_refusal_disconnects_without_spawning_or_signalling(
    old_core: tuple[Path, asyncio.subprocess.Process, asyncio.subprocess.Process],
) -> None:
    runtime, process, agent = old_core
    socket, lock = core_paths(runtime)
    marker = runtime / "replacement-started"
    argv = [sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker)]
    client = CoreClient(str(socket))
    try:
        with pytest.raises(CoreCallError, match=r"incompatible|несовместим") as caught:
            _ = await asyncio.wait_for(client.start_daemon(argv, runtime=runtime), timeout=2)
        assert caught.value.error.message == "PROTOCOL_VERSION_UNSUPPORTED"
        assert not caught.value.error.data.retryable
        assert client._writer is None
        await asyncio.sleep(0.15)
        assert _events(runtime) == ["initialize", "disconnected"]
        assert client.connects == 1
        assert process.returncode is None
        assert agent.returncode is None
        assert lock_held(lock)
        assert not marker.exists()
        assert (runtime / "agent-data").read_bytes() == b"uncommitted agent work\x00\xff"
    finally:
        await client.close()


async def _explicit_retry_can_connect_after_capability_is_available(
    old_core: tuple[Path, asyncio.subprocess.Process, asyncio.subprocess.Process],
) -> None:
    runtime, process, agent = old_core
    socket, _lock = core_paths(runtime)
    client = CoreClient(str(socket))
    try:
        with pytest.raises(CoreCallError):
            _ = await client.connect()
        (runtime / "compatible").touch()
        result = await client.connect()
        assert result.server_pid == process.pid
        assert "work_sessions.v1" in result.capabilities
        assert process.returncode is None
        assert agent.returncode is None
    finally:
        await client.close()
    await asyncio.sleep(0.05)
    assert _events(runtime) == ["initialize", "disconnected", "initialize", "initialized", "disconnected"]


async def _replacement_timeout_preserves_legacy_core_and_its_agent(
    old_core: tuple[Path, asyncio.subprocess.Process, asyncio.subprocess.Process],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, process, agent = old_core
    socket, lock = core_paths(runtime)
    client = CoreClient(str(socket))
    monkeypatch.setattr("aconsole.ui.client.core_client._READY_DEADLINE_S", 0.1)
    try:
        with pytest.raises(CoreCallError):
            _ = await asyncio.wait_for(client.replace_daemon(process.pid, ["unused"], runtime=runtime), timeout=2)
        assert client._writer is None
        assert process.returncode is None
        assert agent.returncode is None
        assert lock_held(lock)
        await asyncio.sleep(0.05)
        assert _events(runtime) == ["SIGTERM", "initialize", "disconnected"]
    finally:
        await client.close()



def test_legacy_refusal_disconnects_without_spawning_or_signalling() -> None:
    async def scenario() -> None:
        async with _old_core() as running:
            await _legacy_refusal_disconnects_without_spawning_or_signalling(running)

    asyncio.run(scenario())


def test_explicit_retry_can_connect_after_capability_is_available() -> None:
    async def scenario() -> None:
        async with _old_core() as running:
            await _explicit_retry_can_connect_after_capability_is_available(running)

    asyncio.run(scenario())


def test_replacement_timeout_preserves_legacy_core_and_its_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _old_core() as running:
            await _replacement_timeout_preserves_legacy_core_and_its_agent(running, monkeypatch)

    asyncio.run(scenario())



def test_reconnect_stops_after_a_legacy_handshake_refusal() -> None:
    async def scenario() -> None:
        async with _old_core() as running:
            runtime, process, agent = running
            socket, _lock = core_paths(runtime)
            (runtime / "compatible").touch()
            client = CoreClient(str(socket))
            try:
                _ = await client.connect()
                with pytest.raises(ConnectionError):
                    _ = await client.request("app/snapshot", EmptyParams(), EmptyParams)
                for _attempt in range(100):
                    if client._closing:
                        break
                    await asyncio.sleep(0.01)
                assert client._closing
                assert client._writer is None
                await asyncio.sleep(0.15)
                assert _events(runtime) == [
                    "initialize", "initialized", "app/snapshot", "disconnected", "initialize", "disconnected",
                ]
                assert process.returncode is None
                assert agent.returncode is None
            finally:
                await client.close()

    asyncio.run(scenario())
