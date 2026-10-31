"""UI client against ``--fake errors`` and the demo burst."""

import asyncio
import fcntl
import gc
import logging
import os
import sys
import tempfile
from pathlib import Path

import pytest

from aconsole.contracts import API_VERSION
from aconsole.contracts.app import EmptyParams, OpenTabParams, SnapshotResult, SubscribeParams, TabIdParams
from aconsole.contracts.common import AgentKind, ProjectId, WorkspaceMode
from aconsole.core.infrastructure.fake.scenarios import APP_EVENT_TYPES
from aconsole.core.infrastructure.fake.server import FakeCore
from aconsole.ui.client.core_client import CoreCallError, CoreClient, core_paths, lock_held
from aconsole.ui.client.methods import Facade, methods_cover_the_table

_CANCELLED = -32800


def _listen(scenario: str, **kwargs: float | bool) -> tuple[FakeCore, str]:
    directory = tempfile.mkdtemp(prefix="aconsole-gui-")
    path = f"{directory}/core.sock"
    core = FakeCore(path, scenario, **kwargs)
    core.start()
    return core, path


def test_method_table_matches_methods_and_version() -> None:
    assert API_VERSION == "1.1.0"
    assert methods_cover_the_table()
    client = CoreClient("unused")
    assert client.timeout_for("knowledge/impact") == 60
    assert client.timeout_for("app/snapshot") == 10


def test_lock_file_reports_a_held_flock() -> None:
    runtime = Path(tempfile.mkdtemp(prefix="aconsole-gui-"))
    _socket, lock = core_paths(runtime)
    lock.parent.mkdir()
    lock.write_text("")
    assert lock_held(lock) is False
    descriptor = os.open(lock, os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert lock_held(lock) is True
    fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)
    assert lock_held(Path(tempfile.mkdtemp(prefix="aconsole-gui-")) / "missing") is False


def test_fake_errors_reject_a_snapshot(fake_core: str) -> None:
    asyncio.run(_reject(fake_core))


def test_demo_events_and_launch_spec(running_app: str) -> None:
    asyncio.run(_demo(running_app))


def test_fake_errors_drop_reconnects() -> None:
    core, path = _listen("errors", drop_after_s=0.2)
    try:
        asyncio.run(_drops(path))
    finally:
        core.stop()


def test_fake_errors_cancel_the_request() -> None:
    core, path = _listen("errors", hold_s=1)
    try:
        asyncio.run(_cancel(path))
    finally:
        core.stop()


def test_fake_errors_resync_after_a_seq_gap() -> None:
    core, path = _listen("errors", gap=True)
    try:
        asyncio.run(_gap(path))
    finally:
        core.stop()


def test_client_starts_the_fake_daemon() -> None:
    asyncio.run(_spawn())


def test_send_failure_during_close_consumes_pending_future_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="asyncio")
    asyncio.run(_send_close_race())
    assert "Future exception was never retrieved" not in caplog.text


async def _reject(path: str) -> None:
    client = CoreClient(path)
    facade = Facade(client)
    try:
        _ = await client.connect()
        with pytest.raises(CoreCallError) as caught:
            _ = await facade.snapshot()
        assert caught.value.error.code != 0
        params = OpenTabParams(
            project_id=ProjectId("p"),
            agent=AgentKind.CLAUDE,
            workspace=WorkspaceMode.MAIN_CHECKOUT,
        )
        with pytest.raises(CoreCallError):
            _ = await facade.open_tab(params)
        with pytest.raises(TypeError):
            _ = await facade.call("app/snapshot", SubscribeParams(after_seq=0))
    finally:
        await client.close()


async def _demo(path: str) -> None:
    client = CoreClient(path)
    facade = Facade(client)
    try:
        _ = await client.connect()
        snapshot, seq = await facade.snapshot()
        _ = await facade.call("events/subscribe", SubscribeParams(after_seq=seq))
        for _ in range(50):
            if client.store.event_types == APP_EVENT_TYPES:
                break
            await asyncio.sleep(0.02)
        assert len(snapshot.projects) == 2
        assert client.store.event_types == APP_EVENT_TYPES
        spec = await facade.launch_spec(TabIdParams(tab_id=snapshot.tabs[0].id))
        assert spec.argv[0] == "/bin/sh"
    finally:
        await client.close()


async def _drops(path: str) -> None:
    client = CoreClient(path, timeout_s=1)
    try:
        _ = await client.connect()
        for _ in range(40):
            if client.connects >= 2:
                break
            await asyncio.sleep(0.05)
        assert client.connects >= 2
        assert client.store.banner == "Подключение к ядру…"
    finally:
        await client.close()


async def _cancel(path: str) -> None:
    client = CoreClient(path, timeout_s=0.05)
    facade = Facade(client)
    try:
        _ = await client.connect()
        with pytest.raises(CoreCallError) as caught:
            _ = await facade.call("app/snapshot", EmptyParams())
        assert caught.value.error.code == _CANCELLED
    finally:
        await client.close()


async def _gap(path: str) -> None:
    client = CoreClient(path, timeout_s=1)
    try:
        _ = await client.connect()
        _ = await client.pull_snapshot()
        for _ in range(40):
            if client.resync_count >= 1:
                break
            await asyncio.sleep(0.02)
        assert client.resync_count >= 1
        assert client.store.snapshot is not None
    finally:
        await client.close()


async def _spawn() -> None:
    directory = tempfile.mkdtemp(prefix="aconsole-gui-")
    path = f"{directory}/core.sock"
    client = CoreClient(path)
    try:
        _ = await client.start_daemon([sys.executable, "-m", "aconsole.core", "--fake", "demo", "--socket", path])
        snapshot, _seq = await Facade(client).snapshot()
        assert len(snapshot.tabs) == 5
        assert client.daemon_pid is not None
    finally:
        await client.close()


async def _send_close_race() -> None:
    send_started = asyncio.Event()
    fail_send = asyncio.Event()

    class BlockedSendClient(CoreClient):
        async def _send(self, payload: bytes) -> None:
            _ = payload
            send_started.set()
            await fail_send.wait()
            message = "send failed"
            raise ConnectionError(message)

    client = BlockedSendClient("unused")
    request = asyncio.create_task(client.request("app/snapshot", EmptyParams(), SnapshotResult))
    await send_started.wait()
    client._fail_pending()
    fail_send.set()
    with pytest.raises(ConnectionError, match="send failed"):
        _ = await request
    del request
    _ = gc.collect()
    await asyncio.sleep(0)
