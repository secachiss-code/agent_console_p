"""UI client against ``--fake errors`` and the demo burst."""

import asyncio
import fcntl
import gc
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

from aconsole.contracts import API_VERSION
from aconsole.contracts.app import EmptyParams, OpenTabParams, SnapshotResult, SubscribeParams, TabIdParams
from aconsole.contracts.common import AgentKind, ProjectId, WorkspaceMode
from aconsole.core.infrastructure.fake.scenarios import APP_EVENT_TYPES
from aconsole.core.infrastructure.fake.server import FakeCore
from aconsole.ui.client.core_client import (
    CoreCallError,
    CoreClient,
    _core_log,
    core_log_path,
    core_paths,
    lock_held,
)
from aconsole.ui.client.methods import Facade, methods_cover_the_table

_CANCELLED = -32800


def _listen(scenario: str, **kwargs: float | bool) -> tuple[FakeCore, str]:
    directory = tempfile.mkdtemp(prefix="aconsole-gui-")
    path = f"{directory}/core.sock"
    core = FakeCore(path, scenario, **kwargs)
    core.start()
    return core, path


def test_method_table_matches_methods_and_version() -> None:
    assert API_VERSION == "1.3.0"
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


def test_held_lock_waits_for_the_socket_and_does_not_spawn() -> None:
    asyncio.run(_held_lock_waits())


def test_held_lock_reports_when_the_socket_never_appears(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aconsole.ui.client.core_client._READY_DEADLINE_S", 0.3)
    started = time.monotonic()
    asyncio.run(_held_lock_never())
    assert time.monotonic() - started < 2


def test_free_lock_still_starts_one_core() -> None:
    asyncio.run(_free_lock_starts())


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


async def _held_lock_waits() -> None:
    runtime = Path(tempfile.mkdtemp(prefix="aconsole-gui-"))
    socket, lock = core_paths(runtime)
    socket.parent.mkdir()
    held = _hold(lock)
    marker = runtime / "spawned"
    late: FakeCore | None = None

    async def listen_late() -> None:
        nonlocal late
        await asyncio.sleep(0.2)
        late = FakeCore(str(socket), "demo")
        await asyncio.to_thread(late.start)

    waiter = asyncio.create_task(listen_late())
    client = CoreClient(str(socket))
    try:
        _ = await client.start_daemon(_marker_argv(marker), runtime=runtime)
        assert client.daemon_pid is None
        assert not marker.exists()
    finally:
        await client.close()
        await waiter
        if late is not None:
            late.stop()
        os.close(held)


async def _held_lock_never() -> None:
    runtime = Path(tempfile.mkdtemp(prefix="aconsole-gui-"))
    socket, lock = core_paths(runtime)
    socket.parent.mkdir()
    held = _hold(lock)
    marker = runtime / "spawned"
    client = CoreClient(str(socket))
    try:
        with pytest.raises(ConnectionError, match="core socket did not appear"):
            _ = await client.start_daemon(_marker_argv(marker), runtime=runtime)
        assert client.daemon_pid is None
        assert not marker.exists()
    finally:
        await client.close()
        os.close(held)


async def _free_lock_starts() -> None:
    runtime = Path(tempfile.mkdtemp(prefix="aconsole-gui-"))
    socket, _lock = core_paths(runtime)
    socket.parent.mkdir()
    client = CoreClient(str(socket))
    try:
        _ = await client.start_daemon(
            [sys.executable, "-m", "aconsole.core", "--fake", "demo", "--socket", str(socket)],
            runtime=runtime,
        )
        assert client.daemon_pid is not None
    finally:
        await client.close()


def _hold(lock: Path) -> int:
    _ = lock.write_text("", encoding="utf-8")
    descriptor = os.open(lock, os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return descriptor


def _marker_argv(marker: Path) -> list[str]:
    return [sys.executable, "-c", "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('x')", str(marker)]


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


def test_core_log_keeps_this_run_and_the_one_before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    log = core_log_path()
    for text in (b"first", b"second", b"third"):
        fd = _core_log()
        assert fd >= 0
        _ = os.write(fd, text)
        os.close(fd)
    assert log == tmp_path / "aconsole" / "core.log"
    assert log.read_bytes() == b"third"
    assert log.with_suffix(".log.1").read_bytes() == b"second"


def test_a_start_that_wrote_nothing_keeps_the_last_real_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    log = core_log_path()
    fd = _core_log()
    _ = os.write(fd, b"real")
    os.close(fd)
    os.close(_core_log())
    os.close(_core_log())
    os.close(_core_log())
    assert log.with_suffix(".log.1").read_bytes() == b"real"
    assert log.read_bytes() == b""


def test_core_starts_without_a_log_it_cannot_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocked))
    assert _core_log() == asyncio.subprocess.DEVNULL
