"""A newer settings notification wins over a delayed get response."""

from __future__ import annotations

import asyncio
from typing import override

import msgspec

from aconsole.contracts.settings import Settings
from aconsole.ui.client.core_client import CoreClient


class _SettingsClient(CoreClient):
    def __init__(self, response: Settings) -> None:
        super().__init__("unused")
        self.response = response
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    @override
    async def request[T: msgspec.Struct](
        self,
        method: str,
        params: msgspec.Struct,
        result_type: type[T],
    ) -> T:
        _ = params
        assert method == "settings/get"
        assert result_type is Settings
        self.started.set()
        await self.release.wait()
        return msgspec.convert(self.response, type=result_type)


def _settings(rev: int, font: str) -> Settings:
    return Settings(
        rev=rev,
        vault_path="/vault",
        worktrees_dir="/worktrees",
        terminal_font=font,
    )


def test_b10_preferences_accept_ignores_lower_revision_without_notifying() -> None:
    from aconsole.ui.viewmodels.setup_vm import PreferencesViewModel

    client = _SettingsClient(_settings(2, "Old"))
    viewmodel = PreferencesViewModel(client)
    notifications: list[Settings] = []
    viewmodel.subscribe(notifications.append)
    current = _settings(4, "Current")
    older = _settings(3, "Older")

    viewmodel.accept(current)
    viewmodel.accept(older)

    assert viewmodel.settings is current
    assert notifications == [current]


def test_b10_preferences_accept_newer_revision_during_load_prevents_rollback() -> None:
    from aconsole.ui.viewmodels.setup_vm import PreferencesViewModel

    async def exercise() -> None:
        older = _settings(5, "Load response")
        newer = _settings(6, "Accepted update")
        client = _SettingsClient(older)
        viewmodel = PreferencesViewModel(client)
        notifications: list[Settings] = []
        viewmodel.subscribe(notifications.append)

        loading = asyncio.create_task(viewmodel.load())
        await asyncio.wait_for(client.started.wait(), timeout=1)
        viewmodel.accept(newer)
        client.release.set()
        loaded = await asyncio.wait_for(loading, timeout=1)

        assert loaded is newer
        assert viewmodel.settings is newer
        assert notifications == [newer]

    asyncio.run(exercise())
