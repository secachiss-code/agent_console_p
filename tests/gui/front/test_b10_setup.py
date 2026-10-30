"""Setup preview/apply state tests using only a fake CoreClient."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, override

import msgspec
import pytest

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

if TYPE_CHECKING:
    from collections.abc import Callable

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


@dataclass
class _PreviewPlan:
    result: FixPreview
    started: asyncio.Event | None = None
    release: asyncio.Event | None = None


@dataclass
class _ChecksPlan:
    result: ChecksResult
    started: asyncio.Event | None = None
    release: asyncio.Event | None = None


@dataclass(frozen=True)
class _Request:
    method: str
    params: msgspec.Struct
    result_type: type[msgspec.Struct]


class _SetupClient(CoreClient):
    def __init__(
        self,
        previews: list[_PreviewPlan],
        checks: list[_ChecksPlan] | None = None,
    ) -> None:
        super().__init__("unused.sock")
        self.previews = previews
        self.checks = checks if checks is not None else []
        self.requests: list[_Request] = []
        self.checks_result = ChecksResult(checks=(_check("storage"),))
        self.apply_started: asyncio.Event | None = None
        self.apply_release: asyncio.Event | None = None
        self.apply_result = _check("storage-fixed", ok=True, fix_id=None)

    async def _checks_response(self) -> ChecksResult:
        if self.checks:
            plan = self.checks.pop(0)
            if plan.started is not None:
                plan.started.set()
            if plan.release is not None:
                await plan.release.wait()
            return plan.result
        return self.checks_result

    @override
    async def request[T: msgspec.Struct](
        self,
        method: str,
        params: msgspec.Struct,
        result_type: type[T],
        *,
        long: bool = False,
    ) -> T:
        _ = long
        self.requests.append(_Request(method, params, result_type))
        if method == "setup/checks":
            return await self._checks_response()  # pyright: ignore[reportReturnType] -- the fake returns the configured contract result
        if method == "setup/previewFix":
            plan = self.previews.pop(0)
            if plan.started is not None:
                plan.started.set()
            if plan.release is not None:
                await plan.release.wait()
            return plan.result  # pyright: ignore[reportReturnType] -- the fake returns the configured contract result
        if method == "setup/applyFix":
            if self.apply_started is not None:
                self.apply_started.set()
            if self.apply_release is not None:
                await self.apply_release.wait()
            return self.apply_result  # pyright: ignore[reportReturnType] -- the fake returns the configured contract result
        message = f"unexpected setup request: {method}"
        raise AssertionError(message)


@dataclass
class _UpdatePlan:
    result: Settings
    started: asyncio.Event | None = None
    release: asyncio.Event | None = None
    error: Exception | None = None


class _PreferencesClient(CoreClient):
    def __init__(self, settings: Settings, updates: list[_UpdatePlan]) -> None:
        super().__init__("unused.sock")
        self.settings_result = settings
        self.updates = updates
        self.requests: list[_Request] = []

    @override
    async def request[T: msgspec.Struct](
        self,
        method: str,
        params: msgspec.Struct,
        result_type: type[T],
        *,
        long: bool = False,
    ) -> T:
        _ = long
        self.requests.append(_Request(method, params, result_type))
        if method == "settings/get":
            return self.settings_result  # pyright: ignore[reportReturnType] -- the fake returns the configured contract result
        if method == "settings/update":
            plan = self.updates.pop(0)
            if plan.started is not None:
                plan.started.set()
            if plan.release is not None:
                await plan.release.wait()
            if plan.error is not None:
                raise plan.error
            return plan.result  # pyright: ignore[reportReturnType] -- the fake returns the configured contract result
        message = f"unexpected preferences request: {method}"
        raise AssertionError(message)


def _check(
    check_id: str,
    *,
    ok: bool = False,
    fix_id: str | None = "fix-storage",
) -> CheckResult:
    return CheckResult(
        id=check_id,
        title="Storage",
        ok=ok,
        severity="error" if not ok else "info",
        detail=None,
        fix_id=fix_id,
        requires_confirmation=fix_id is not None,
    )


def _preview(
    fix_id: str = "fix-storage",
    token: str | None = None,
    *,
    expires_at: datetime | None = None,
) -> FixPreview:
    return FixPreview(
        fix_id=fix_id,
        summary="Repair storage configuration",
        diff="before -> after",
        commands=("repair-storage",),
        reversible=True,
        confirm_token=token if token is not None else secrets.token_urlsafe(24),
        expires_at=expires_at or NOW + timedelta(minutes=5),
    )


def _settings(
    rev: int,
    *,
    terminal_font: str | None = None,
    theme: Literal["system", "light", "dark"] = "system",
) -> Settings:
    return Settings(
        rev=rev,
        vault_path="/vault",
        worktrees_dir="/worktrees",
        terminal_font=terminal_font,
        theme=theme,
    )


def _observe(values: list[Settings]) -> Callable[[Settings], None]:
    def callback(settings: Settings) -> None:
        values.append(settings)

    return callback


def test_b10_setup_load_and_preview_never_apply_without_explicit_action() -> None:
    from aconsole.ui.viewmodels.setup_vm import SetupViewModel

    async def exercise() -> None:
        preview = _preview()
        client = _SetupClient([_PreviewPlan(preview)])
        view_model = SetupViewModel(client)

        await view_model.load_checks()
        await view_model.preview_fix(preview.fix_id)

        assert view_model.checks == client.checks_result.checks
        assert view_model.preview is preview
        assert [request.method for request in client.requests] == [
            "setup/checks",
            "setup/previewFix",
        ]
        assert client.requests[0] == _Request("setup/checks", EmptyParams(), ChecksResult)
        assert client.requests[1] == _Request(
            "setup/previewFix", FixIdParams(fix_id=preview.fix_id), FixPreview
        )

    asyncio.run(exercise())


def test_b10_setup_checks_keep_newer_result_when_older_request_finishes_late() -> None:
    from aconsole.ui.viewmodels.setup_vm import SetupViewModel

    async def exercise() -> None:
        older_started = asyncio.Event()
        release_older = asyncio.Event()
        older_checks = ChecksResult(checks=(_check("old-check"),))
        newer_checks = ChecksResult(checks=(_check("new-check", ok=True, fix_id=None),))
        client = _SetupClient(
            [],
            checks=[
                _ChecksPlan(older_checks, older_started, release_older),
                _ChecksPlan(newer_checks),
            ],
        )
        view_model = SetupViewModel(client)

        older_load = asyncio.create_task(view_model.load_checks())
        await asyncio.wait_for(older_started.wait(), timeout=1)
        await view_model.load_checks()

        assert view_model.checks == newer_checks.checks

        release_older.set()
        await older_load

        assert view_model.checks == newer_checks.checks

    asyncio.run(exercise())


def test_b10_setup_apply_uses_exact_visible_preview_id_and_token() -> None:
    from aconsole.ui.viewmodels.setup_vm import SetupViewModel

    async def exercise() -> None:
        preview = _preview("visible-fix")
        client = _SetupClient([_PreviewPlan(preview)])
        view_model = SetupViewModel(client)
        await view_model.preview_fix("visible-fix")

        result = await view_model.apply_preview(NOW)

        assert result is client.apply_result
        assert client.requests[-1] == _Request(
            "setup/applyFix",
            ApplyFixParams(fix_id=preview.fix_id, confirm_token=preview.confirm_token),
            CheckResult,
        )
        assert view_model.preview is None

    asyncio.run(exercise())


def test_b10_setup_rejects_absent_and_expired_previews_without_apply() -> None:
    from aconsole.ui.viewmodels.setup_vm import SetupViewModel

    async def exercise() -> None:
        absent_client = _SetupClient([])
        absent_vm = SetupViewModel(absent_client)
        with pytest.raises(ValueError, match="no applicable preview"):
            await absent_vm.apply_preview(NOW)

        expired = _preview(expires_at=NOW)
        expired_client = _SetupClient([_PreviewPlan(expired)])
        expired_vm = SetupViewModel(expired_client)
        await expired_vm.preview_fix(expired.fix_id)
        with pytest.raises(ValueError, match="no applicable preview"):
            await expired_vm.apply_preview(NOW)

        assert all(request.method != "setup/applyFix" for request in absent_client.requests)
        assert all(request.method != "setup/applyFix" for request in expired_client.requests)

    asyncio.run(exercise())


def test_b10_setup_latest_preview_wins_when_older_response_arrives_late() -> None:
    from aconsole.ui.viewmodels.setup_vm import SetupViewModel

    async def exercise() -> None:
        older_started = asyncio.Event()
        release_older = asyncio.Event()
        older = _preview("older-fix")
        newer = _preview("newer-fix")
        client = _SetupClient(
            [
                _PreviewPlan(older, older_started, release_older),
                _PreviewPlan(newer),
            ]
        )
        view_model = SetupViewModel(client)
        old_request = asyncio.create_task(view_model.preview_fix(older.fix_id))
        await asyncio.wait_for(older_started.wait(), timeout=1)

        await view_model.preview_fix(newer.fix_id)
        release_older.set()
        await old_request

        assert view_model.preview is newer
        assert [request.params for request in client.requests] == [
            FixIdParams(fix_id="older-fix"),
            FixIdParams(fix_id="newer-fix"),
        ]

    asyncio.run(exercise())


def test_b10_setup_discard_invalidates_pending_preview() -> None:
    from aconsole.ui.viewmodels.setup_vm import SetupViewModel

    async def exercise() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        pending = _preview()
        client = _SetupClient([_PreviewPlan(pending, started, release)])
        view_model = SetupViewModel(client)
        request = asyncio.create_task(view_model.preview_fix(pending.fix_id))
        await asyncio.wait_for(started.wait(), timeout=1)

        view_model.discard_preview()
        release.set()
        await request

        assert view_model.preview is None

    asyncio.run(exercise())


def test_b10_setup_blocks_double_apply_while_first_call_is_pending() -> None:
    from aconsole.ui.viewmodels.setup_vm import SetupViewModel

    async def exercise() -> None:
        preview = _preview()
        started = asyncio.Event()
        release = asyncio.Event()
        client = _SetupClient([_PreviewPlan(preview)])
        client.apply_started = started
        client.apply_release = release
        view_model = SetupViewModel(client)
        await view_model.preview_fix(preview.fix_id)
        first_apply = asyncio.create_task(view_model.apply_preview(NOW))
        await asyncio.wait_for(started.wait(), timeout=1)

        assert view_model.applying
        with pytest.raises(ValueError, match="no applicable preview"):
            await view_model.apply_preview(NOW)
        release.set()

        result = await first_apply
        assert result is client.apply_result
        assert not view_model.applying
        assert sum(request.method == "setup/applyFix" for request in client.requests) == 1

    asyncio.run(exercise())


def test_b10_preferences_load_and_update_publish_server_settings_to_two_subscribers() -> None:
    from aconsole.ui.viewmodels.setup_vm import PreferencesViewModel

    async def exercise() -> None:
        initial = _settings(4, terminal_font="Initial Font")
        updated = _settings(5, terminal_font=None)
        patch = SettingsPatch(terminal_font=None)
        client = _PreferencesClient(initial, [_UpdatePlan(updated)])
        view_model = PreferencesViewModel(client)
        first_seen: list[Settings] = []
        second_seen: list[Settings] = []
        view_model.subscribe(_observe(first_seen))
        view_model.subscribe(_observe(second_seen))

        loaded = await view_model.load()
        result = await view_model.update(patch)

        assert loaded is initial
        assert result is updated
        assert view_model.settings is updated
        assert first_seen == [initial, updated]
        assert second_seen == [initial, updated]
        assert client.requests == [
            _Request("settings/get", EmptyParams(), Settings),
            _Request(
                "settings/update",
                SettingsUpdateParams(patch=patch, rev=4),
                Settings,
            ),
        ]

    asyncio.run(exercise())


def test_b10_preferences_distinguish_none_from_unset_and_use_returned_revisions() -> None:
    from aconsole.ui.viewmodels.setup_vm import PreferencesViewModel

    async def exercise() -> None:
        initial = _settings(10, terminal_font="Old Font")
        cleared = _settings(11, terminal_font=None)
        canonical = _settings(13, terminal_font="Server Font")
        clear_patch = SettingsPatch(terminal_font=None)
        unchanged_patch = SettingsPatch()
        client = _PreferencesClient(
            initial, [_UpdatePlan(cleared), _UpdatePlan(canonical)]
        )
        view_model = PreferencesViewModel(client)
        await view_model.load()

        first = await view_model.update(clear_patch)
        second = await view_model.update(unchanged_patch)

        assert clear_patch.terminal_font is None
        assert unchanged_patch.terminal_font is msgspec.UNSET
        assert first is cleared
        assert second is canonical
        assert view_model.settings is canonical
        update_requests = [
            request for request in client.requests if request.method == "settings/update"
        ]
        assert update_requests == [
            _Request(
                "settings/update",
                SettingsUpdateParams(patch=clear_patch, rev=10),
                Settings,
            ),
            _Request(
                "settings/update",
                SettingsUpdateParams(patch=unchanged_patch, rev=11),
                Settings,
            ),
        ]

    asyncio.run(exercise())


def test_b10_preferences_serialize_concurrent_updates_using_latest_revision() -> None:
    from aconsole.ui.viewmodels.setup_vm import PreferencesViewModel

    async def exercise() -> None:
        initial = _settings(20)
        first_result = _settings(21, theme="dark")
        second_result = _settings(22, terminal_font="Updated Font")
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        second_started = asyncio.Event()
        release_second = asyncio.Event()
        client = _PreferencesClient(
            initial,
            [
                _UpdatePlan(first_result, first_started, release_first),
                _UpdatePlan(second_result, second_started, release_second),
            ],
        )
        view_model = PreferencesViewModel(client)
        await view_model.load()
        first_patch = SettingsPatch(theme="dark")
        second_patch = SettingsPatch(terminal_font="Updated Font")
        first_update = asyncio.create_task(view_model.update(first_patch))
        await asyncio.wait_for(first_started.wait(), timeout=1)
        second_update = asyncio.create_task(view_model.update(second_patch))

        assert not second_started.is_set()
        release_first.set()
        await asyncio.wait_for(second_started.wait(), timeout=1)
        updates = [
            request for request in client.requests if request.method == "settings/update"
        ]
        assert updates == [
            _Request(
                "settings/update",
                SettingsUpdateParams(patch=first_patch, rev=20),
                Settings,
            ),
            _Request(
                "settings/update",
                SettingsUpdateParams(patch=second_patch, rev=21),
                Settings,
            ),
        ]
        release_second.set()

        assert await first_update is first_result
        assert await second_update is second_result
        assert view_model.settings is second_result

    asyncio.run(exercise())


def test_b10_preferences_update_error_propagates_without_publication() -> None:
    from aconsole.ui.viewmodels.setup_vm import PreferencesViewModel

    async def exercise() -> None:
        initial = _settings(30, terminal_font="Stable Font")
        error = RuntimeError("settings conflict")
        client = _PreferencesClient(
            initial, [_UpdatePlan(_settings(31), error=error)]
        )
        view_model = PreferencesViewModel(client)
        seen: list[Settings] = []
        view_model.subscribe(_observe(seen))
        await view_model.load()

        with pytest.raises(RuntimeError, match="settings conflict") as raised:
            await view_model.update(SettingsPatch(terminal_font=None))

        assert raised.value is error
        assert view_model.settings is initial
        assert seen == [initial]

    asyncio.run(exercise())


def test_b10_preferences_unsubscribe_is_idempotent() -> None:
    from aconsole.ui.viewmodels.setup_vm import PreferencesViewModel

    async def exercise() -> None:
        settings = _settings(40)
        client = _PreferencesClient(settings, [])
        view_model = PreferencesViewModel(client)
        removed_seen: list[Settings] = []
        retained_seen: list[Settings] = []
        unsubscribe = view_model.subscribe(_observe(removed_seen))
        view_model.subscribe(_observe(retained_seen))

        unsubscribe()
        unsubscribe()
        await view_model.load()

        assert removed_seen == []
        assert retained_seen == [settings]

    asyncio.run(exercise())
