"""Limits visibility remains a user preference across core restarts."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aconsole.contracts.app import EmptyParams, SettingsUpdateParams
from aconsole.contracts.common import AgentKind
from aconsole.contracts.settings import Settings, SettingsPatch
from tests.core.test_wire import _connect, _daemon, _stop  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    from pathlib import Path


def test_quota_visibility_defaults_to_showing_all_agents_and_persists(tmp_path: Path) -> None:
    async def scenario() -> None:
        hidden = frozenset({AgentKind.CLAUDE, AgentKind.CODEX})
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            current = await client.call("settings/get", EmptyParams(), Settings)
            assert not current.quota_hidden_agents
            patch = SettingsPatch(quota_hidden_agents=hidden, quota_warn_pct=0, quota_critical_pct=0)
            saved = await client.call("settings/update", SettingsUpdateParams(rev=current.rev, patch=patch), Settings)
            assert saved.quota_hidden_agents == hidden
        finally:
            await _stop(daemon, task, client)

        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            restored = await client.call("settings/get", EmptyParams(), Settings)
            assert restored.quota_hidden_agents == hidden
            assert restored.quota_warn_pct == restored.quota_critical_pct == 0
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())
