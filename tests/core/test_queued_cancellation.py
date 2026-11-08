"""A timed-out queued mutation must not execute later on the tab worker."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING

import pytest

from aconsole.contracts.app import ChecksResult, EmptyParams, SettingsUpdateParams
from aconsole.contracts.settings import Settings, SettingsPatch
from aconsole.core.application.services.setup import SetupService
from aconsole.ui.client.core_client import CoreCallError, CoreClient
from tests.core.test_core_latency import _running

if TYPE_CHECKING:
    from pathlib import Path


def test_client_timeout_cancels_a_queued_settings_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _running(tmp_path, monkeypatch) as core:
            client = CoreClient(str(core.daemon.socket))
            entered, release = threading.Event(), threading.Event()

            def delayed(_self: SetupService) -> ChecksResult:
                entered.set()
                _ = release.wait(2)
                return ChecksResult(checks=())

            monkeypatch.setattr(SetupService, "checks", delayed)
            _ = await client.connect()
            settings = await core.client.call("settings/get", EmptyParams(), Settings)
            checking = asyncio.create_task(core.client.call("setup/checks", EmptyParams(), ChecksResult))
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                client.timeout_s = 0.05
                before = time.perf_counter()
                with pytest.raises(CoreCallError) as error:
                    _ = await client.request(
                        "settings/update",
                        SettingsUpdateParams(
                            rev=settings.rev,
                            patch=SettingsPatch(approvals_mode="native"),
                        ),
                        Settings,
                    )
                assert error.value.error.message == "REQUEST_CANCELLED"
                assert time.perf_counter() - before < 0.5
                release.set()
                await checking
                current = await core.client.call("settings/get", EmptyParams(), Settings)
                assert current.rev == settings.rev
                assert current.approvals_mode == settings.approvals_mode
            finally:
                release.set()
                await checking
                await client.close()

    asyncio.run(scenario())
