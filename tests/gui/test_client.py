"""Client subscription, a second resync, and run_app."""

import asyncio
import tempfile

from aconsole.contracts.events import UiEvent
from aconsole.core.infrastructure.fake.server import FakeCore
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.client.methods import Facade
from aconsole.ui.client.store import CoreStore, event_name
from tests.gui.conftest import UiApp


def test_client_subscribe_sees_every_event(ui_app: UiApp) -> None:
    assert isinstance(ui_app, UiApp)
    asyncio.run(_subscribe(ui_app.socket))


def test_client_second_drop_resyncs_again() -> None:
    directory = tempfile.mkdtemp(prefix="aconsole-gui-")
    path = f"{directory}/core.sock"
    core = FakeCore(path, "empty", drop_after_s=0.25)
    core.start()
    try:
        asyncio.run(_second_drop(path))
    finally:
        core.stop()


def test_client_timeout_does_not_stop_reconnect() -> None:
    directory = tempfile.mkdtemp(prefix="aconsole-gui-")
    path = f"{directory}/core.sock"
    core = FakeCore(path, "errors", hold_s=1)
    core.start()
    try:
        asyncio.run(_timeout_reconnect(path))
    finally:
        core.stop()


def test_client_run_app_connects_to_demo(ui_app: UiApp) -> None:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    from aconsole.ui.bootstrap import run_app, spawn_task

    seen = {"projects": 0}

    def create_connected() -> Gio.Application:
        app = Gio.Application(
            application_id="dev.aconsole.ClientTest",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )

        def on_activate(application: Gio.Application) -> None:
            application.hold()

            async def connect() -> None:
                client = CoreClient(ui_app.socket)
                try:
                    await client.connect()
                    snapshot, _seq = await Facade(client).snapshot()
                    seen["projects"] = len(snapshot.projects)
                finally:
                    await client.close()
                    application.release()
                    application.quit()

            _ = spawn_task(connect())

        _ = app.connect("activate", on_activate)
        return app

    assert run_app(create_connected, []) == 0
    assert seen["projects"] == 2


async def _subscribe(path: str) -> None:
    client = CoreClient(path)
    facade = Facade(client)
    seen: list[str] = []
    def remember(event: UiEvent, state: CoreStore) -> None:
        assert isinstance(event, UiEvent)
        assert isinstance(state, CoreStore)
        seen.append(event_name(event))
        _ = state.seq

    _ = facade.subscribe(remember)
    try:
        await client.connect()
        await client.pull_snapshot()
        for _ in range(50):
            if len(seen) >= 21:
                break
            await asyncio.sleep(0.02)
        assert len(seen) >= 21
        assert client.store.snapshot is not None
        assert len(client.store.snapshot.tabs) == 5
        assert client.resync_count >= 1
    finally:
        await client.close()


async def _second_drop(path: str) -> None:
    client = CoreClient(path, timeout_s=1)
    try:
        await client.connect()
        await client.pull_snapshot()
        for _ in range(40):
            if client.resync_count >= 2:
                break
            await asyncio.sleep(0.1)
        assert client.resync_count >= 2
    finally:
        await client.close()


async def _timeout_reconnect(path: str) -> None:
    client = CoreClient(path, timeout_s=0.05)
    try:
        await client.connect()
        writer = client._writer
        assert writer is not None
        writer.close()
        await writer.wait_closed()
        for _ in range(40):
            if client.connects >= 2:
                break
            await asyncio.sleep(0.05)
        assert client.connects >= 2
        assert client._reconnect_task is not None
    finally:
        await client.close()
