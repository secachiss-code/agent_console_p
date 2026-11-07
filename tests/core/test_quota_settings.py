"""Custom quota thresholds take effect through real settings RPC and survive restart."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import msgspec
import pytest

from aconsole.contracts.app import AccountView, EmptyParams, QuotaWindowView, SettingsUpdateParams
from aconsole.contracts.common import AccountId, AgentKind, QuotaLevel
from aconsole.contracts.events import QuotaUpdated
from aconsole.contracts.settings import Settings, SettingsPatch, commit_settings
from tests.core.test_wire import _connect, _daemon, _stop

if TYPE_CHECKING:
    from pathlib import Path


def test_invalid_quota_threshold_order_is_refused() -> None:
    settings = Settings(rev=1, vault_path="", worktrees_dir="")
    with pytest.raises(ValueError, match="threshold"):
        commit_settings(settings, SettingsPatch(quota_warn_pct=99, quota_critical_pct=50))


def test_settings_reclassify_existing_and_future_observations(tmp_path: Path) -> None:
    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        original_agents = daemon._agents_source
        daemon._agents_source = lambda source=original_agents: tuple(
            msgspec.structs.replace(agent, available=True, logged_in=True) for agent in source()
        )
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        now = datetime.now(UTC)
        event = QuotaUpdated(account=AccountView(id=AccountId("custom"), agent=AgentKind.CLAUDE, plan_label="Test",
            quota=(QuotaWindowView(key="5h", used_pct=60.0, resets_at=now + timedelta(hours=1),
                observed_at=now, level=QuotaLevel.OK, estimated=False),)))
        daemon._account_observed(event)
        current = await client.call("settings/get", EmptyParams(), Settings)
        _ = await client.call("settings/update", SettingsUpdateParams(rev=current.rev,
            patch=SettingsPatch(quota_warn_pct=40, quota_critical_pct=50)), Settings)
        snapshot = await client.snapshot()
        account = next(row for row in snapshot.accounts if row.id == event.account.id)
        assert account.quota[0].level is QuotaLevel.CRITICAL
        await _stop(daemon, task, client)
        daemon = _daemon(tmp_path, serve_ingest=False)
        original_agents = daemon._agents_source
        daemon._agents_source = lambda source=original_agents: tuple(
            msgspec.structs.replace(agent, available=True, logged_in=True) for agent in source()
        )
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        daemon._account_observed(event)
        snapshot = await client.snapshot()
        account = next(row for row in snapshot.accounts if row.id == event.account.id)
        assert account.quota[0].level is QuotaLevel.CRITICAL
        restored = await client.call("settings/get", EmptyParams(), Settings)
        _ = await client.call("settings/update", SettingsUpdateParams(rev=restored.rev,
            patch=SettingsPatch(quota_warn_pct=70, quota_critical_pct=90)), Settings)
        snapshot = await client.snapshot()
        account = next(row for row in snapshot.accounts if row.id == event.account.id)
        assert account.quota[0].level is QuotaLevel.OK
        await _stop(daemon, task, client)

    asyncio.run(scenario())
