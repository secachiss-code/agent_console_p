"""Setup check and explicitly confirmed fix state."""

import asyncio
from collections.abc import Callable
from datetime import datetime

from aconsole.contracts.app import (
    ApplyFixParams,
    CheckResult,
    ChecksResult,
    EmptyParams,
    FixIdParams,
    FixPreview,
    SettingsUpdateParams,
)
from aconsole.contracts.settings import Settings, SettingsPatch
from aconsole.ui.client.core_client import CoreClient


class SetupViewModel:
    def __init__(self, client: CoreClient) -> None:
        self._client: CoreClient = client
        self._generation: int = 0
        self._checks_generation: int = 0
        self.checks: tuple[CheckResult, ...] = ()
        self.preview: FixPreview | None = None
        self.applying: bool = False

    async def load_checks(self) -> None:
        self._checks_generation += 1
        generation = self._checks_generation
        result = await self._client.request("setup/checks", EmptyParams(), ChecksResult, long=True)
        if generation == self._checks_generation:
            self.checks = result.checks

    async def preview_fix(self, fix_id: str) -> None:
        self._generation += 1
        generation = self._generation
        self.preview = None
        result = await self._client.request("setup/previewFix", FixIdParams(fix_id=fix_id), FixPreview)
        if generation == self._generation:
            if result.fix_id != fix_id:
                message = "preview does not match requested fix"
                raise ValueError(message)
            self.preview = result

    def discard_preview(self) -> None:
        self._generation += 1
        self.preview = None

    async def apply_preview(self, now: datetime) -> CheckResult:
        preview = self.preview
        if self.applying or preview is None or preview.expires_at <= now:
            message = "no applicable preview"
            raise ValueError(message)
        self.discard_preview()
        self.applying = True
        try:
            return await self._client.request(
                "setup/applyFix",
                ApplyFixParams(fix_id=preview.fix_id, confirm_token=preview.confirm_token),
                CheckResult,
                long=True,
            )
        finally:
            self.applying = False


class PreferencesViewModel:
    """Publish server-confirmed settings and serialize revision-based updates."""

    def __init__(self, client: CoreClient) -> None:
        self._client: CoreClient = client
        self._lock: asyncio.Lock = asyncio.Lock()
        self._listeners: list[Callable[[Settings], None]] = []
        self.settings: Settings | None = None

    def subscribe(self, callback: Callable[[Settings], None]) -> Callable[[], None]:
        self._listeners.append(callback)
        active = True

        def unsubscribe() -> None:
            nonlocal active
            if active:
                active = False
                self._listeners.remove(callback)

        return unsubscribe

    def _publish(self, settings: Settings) -> None:
        self.settings = settings
        for callback in tuple(self._listeners):
            callback(settings)

    async def _load(self) -> Settings:
        settings = await self._client.request("settings/get", EmptyParams(), Settings)
        self._publish(settings)
        return settings

    async def load(self) -> Settings:
        async with self._lock:
            return await self._load()

    async def update(self, patch: SettingsPatch) -> Settings:
        async with self._lock:
            current = self.settings if self.settings is not None else await self._load()
            settings = await self._client.request(
                "settings/update", SettingsUpdateParams(patch=patch, rev=current.rev), Settings,
            )
            self._publish(settings)
            return settings
