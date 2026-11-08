"""The window's client against the real core: a held hook is answered through the panel's calls."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import msgspec

from aconsole.contracts.common import ApprovalDecision, ApprovalScope
from aconsole.contracts.ingest import HookResponse
from aconsole.core.bootstrap import Daemon
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.viewmodels.approvals_vm import ApprovalsViewModel
from tests.core.conftest import wait_for_socket
from tests.core.test_approval_hold import _post_daemon_permission_hook, _seed_daemon_tab
from tests.socket_paths import short_socket_path

if TYPE_CHECKING:
    from pathlib import Path

    from aconsole.contracts.app import ApprovalView

_TAB = "01JBF5SW7QNZ2X5QR8M6K9H000"
_TOKEN = "test-tab-token"  # noqa: S105 -- only this test's daemon sees it
_HOOK = (
    b'{"session_id":"window-session","hook_event_name":"PermissionRequest",'
    b'"tool_name":"Bash","tool_input":{"command":"git status"}}'
)


def _run(tmp_path: Path, decide: ApprovalDecision | None) -> bytes:
    async def scenario() -> bytes:
        database = tmp_path / "aconsole.db"
        _seed_daemon_tab(database, tmp_path, _TAB)
        daemon = Daemon(short_socket_path(tmp_path / "core.sock"), idle_s=30, database=database)
        daemon.serve_ingest = True
        daemon._tokens.register(_TAB, _TOKEN)
        serving = asyncio.create_task(daemon.serve())
        await wait_for_socket(daemon.socket)
        client = CoreClient(str(daemon.socket))
        try:
            negotiated = await client.connect()
            assert "approvals.inline" in negotiated.capabilities
            _ = await client.pull_snapshot()
            assert daemon.ingest_port is not None
            hook = asyncio.create_task(
                asyncio.to_thread(_post_daemon_permission_hook, daemon.ingest_port, _TOKEN, _TAB, _HOOK),
            )
            view = await _held(client)
            assert not hook.done(), "the hook returned before the window answered"
            assert view.summary
            viewmodel = ApprovalsViewModel(client)
            if decide is None:
                _ = await viewmodel.release(view)
            else:
                _ = await viewmodel.decide(view, decide, ApprovalScope.ONCE)
            status, body = await asyncio.wait_for(hook, 5)
            assert status == 200
            await _gone(client)
            return body
        finally:
            await client.close()
            daemon.request_stop()
            await asyncio.wait_for(serving, 15)

    return asyncio.run(scenario())


async def _held(client: CoreClient) -> ApprovalView:
    for _ in range(250):
        snapshot = client.store.snapshot
        if snapshot is not None and snapshot.approvals:
            return snapshot.approvals[0]
        await asyncio.sleep(0.02)
    message = "the request never reached the window"
    raise AssertionError(message)


async def _gone(client: CoreClient) -> None:
    for _ in range(250):
        snapshot = client.store.snapshot
        if snapshot is not None and not snapshot.approvals:
            return
        await asyncio.sleep(0.02)
    message = "the window kept a closed request"
    raise AssertionError(message)


def test_allow_reaches_the_agent(tmp_path: Path) -> None:
    body = _run(tmp_path, ApprovalDecision.ALLOW)
    assert msgspec.json.decode(body, type=HookResponse).hook_specific_output.decision.behavior == "allow"


def test_deny_reaches_the_agent(tmp_path: Path) -> None:
    body = _run(tmp_path, ApprovalDecision.DENY)
    assert msgspec.json.decode(body, type=HookResponse).hook_specific_output.decision.behavior == "deny"


def test_release_leaves_the_agent_to_ask(tmp_path: Path) -> None:
    assert _run(tmp_path, None).strip() == b"{}"
