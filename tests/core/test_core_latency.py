"""HTTP/IPC approvals stay responsive while real checkpoint and MCP work is stalled."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec

from aconsole.contracts.app import (
    ChecksResult,
    DecideParams,
    EmptyParams,
    ProjectAddParams,
    ProjectView,
    SettingsUpdateParams,
    SubscribeParams,
    TabIdParams,
    TabView,
    TerminalLaunchSpec,
)
from aconsole.contracts.common import ApprovalDecision, ClientRole
from aconsole.contracts.events import ApprovalRequested
from aconsole.contracts.ipc import InitializeParams, ProtocolVersion
from aconsole.contracts.settings import Settings, SettingsPatch
from aconsole.core.application.services.setup import SetupService
from aconsole.core.infrastructure.git.checkpoints import GitCheckpoints
from aconsole.core.infrastructure.mcp import tools
from tests.core.git_helpers import commit
from tests.core.test_approval_hold import _daemon_event, _daemon_rpc, _post_daemon_permission_hook
from tests.core.test_claude_mcp import _config, _content, _Mcp
from tests.core.test_review_wire import _hook
from tests.core.test_wire import _connect, _daemon, _git_repo, _open, _stop

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

    from aconsole.core.application.ports.checkpoint_store import TreeCapture
    from aconsole.core.bootstrap import Daemon
    from tests.core.test_wire import _Client

_LIMIT_S = 0.25
_STALL_S = 1.0


@dataclass
class _Running:
    daemon: Daemon
    client: _Client
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    tab: TabView
    spec: TerminalLaunchSpec
    repo: Path

    @property
    def port(self) -> int:
        port = self.daemon.ingest_port
        assert port is not None
        return port


@asynccontextmanager
async def _running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Running]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FAKE_HOME", str(home))
    repo = _git_repo(tmp_path / "repo")
    _ = commit(repo, "README.md", "base\n", "seed")
    daemon = _daemon(tmp_path)
    task = asyncio.create_task(daemon.serve())
    client = await _connect(daemon.socket)
    reader, writer = await asyncio.open_unix_connection(daemon.socket)
    try:
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        tab = await client.call("tab/open", _open(project.id), TabView)
        spec = await client.call("tab/launchSpec", TabIdParams(tab_id=tab.id), TerminalLaunchSpec)
        await _daemon_rpc(
            reader,
            writer,
            1,
            "initialize",
            InitializeParams(
                protocol=(ProtocolVersion(major=1, minor=0), ProtocolVersion(major=1, minor=0)),
                role=ClientRole.UI,
                client_name="latency",
                client_version="1",
                pid=1,
                capabilities=frozenset({"events.v1", "approvals.inline"}),
            ),
        )
        await _daemon_rpc(reader, writer, 2, "events/subscribe", SubscribeParams(after_seq=0))
        yield _Running(daemon, client, reader, writer, tab, spec, repo)
    finally:
        writer.close()
        await writer.wait_closed()
        await _stop(daemon, task, client)


def _measure(name: str, value: float) -> None:
    directory = Path(__file__).parents[2] / ".aconsole-dev/core2-latency"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps({"seconds": value, "limit_seconds": _LIMIT_S}) + "\n")


async def _permission(core: _Running) -> asyncio.Task[tuple[int, bytes]]:
    body = msgspec.json.encode(
        {
            "hook_event_name": "PermissionRequest",
            "session_id": "latency",
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
        }
    )
    return asyncio.create_task(
        asyncio.to_thread(
            _post_daemon_permission_hook, core.port, core.spec.env["ACONSOLE_TAB_TOKEN"], str(core.tab.id), body
        )
    )


async def _decide(core: _Running, request_id: int) -> None:
    while True:
        event, _ = await asyncio.wait_for(_daemon_event(core.reader), 3)
        if isinstance(event.event, ApprovalRequested):
            approval = event.event.approval
            break
    await _daemon_rpc(
        core.reader,
        core.writer,
        request_id,
        "approval/decide",
        DecideParams(
            approval_id=approval.id,
            nonce=approval.nonce,
            decision=ApprovalDecision.DENY,
        ),
    )


def test_permission_request_and_decision_do_not_wait_for_a_large_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with _running(tmp_path, monkeypatch) as core:
            folder = Path(core.tab.worktree_path) / "bulk"
            folder.mkdir()
            for index in range(1500):
                (folder / f"{index}.txt").write_text("data\n" * 800)
            entered, release = threading.Event(), threading.Event()
            capture = GitCheckpoints.capture

            def delayed(self: GitCheckpoints, repo: Path, ref: str, label: str) -> TreeCapture:
                if not entered.is_set():
                    entered.set()
                    _ = release.wait(_STALL_S)
                before = time.perf_counter()
                result = capture(self, repo, ref, label)
                assert len(result.files) >= 1500
                _measure("git_capture", time.perf_counter() - before)
                return result

            monkeypatch.setattr(GitCheckpoints, "capture", delayed)
            hook = None
            try:
                await asyncio.to_thread(
                    _hook,
                    core.port,
                    str(core.tab.id),
                    core.spec.env["ACONSOLE_TAB_TOKEN"],
                    {"hook_event_name": "UserPromptSubmit", "session_id": "latency", "prompt": "snapshot"},
                )
                assert await asyncio.to_thread(entered.wait, 3)
                before = time.perf_counter()
                hook = await _permission(core)
                await _decide(core, 3)
                status, _body = await asyncio.wait_for(hook, 3)
                elapsed = time.perf_counter() - before
                _measure("approval_during_checkpoint", elapsed)
                assert status == 200
                assert elapsed < _LIMIT_S
            finally:
                release.set()
                if hook is not None:
                    await asyncio.gather(hook, return_exceptions=True)

    asyncio.run(scenario())


def test_slow_mcp_git_does_not_block_the_event_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _running(tmp_path, monkeypatch) as core:
            _path, config = _config(core.spec.argv)
            mcp = _Mcp(config, core.spec.env)
            await asyncio.to_thread(mcp.start)
            entered, release = threading.Event(), threading.Event()
            verified = tools._verified

            def delayed(worktree: Path | None) -> str | None:
                entered.set()
                _ = release.wait(_STALL_S)
                return verified(worktree)

            monkeypatch.setattr(tools, "_verified", delayed)
            before = time.perf_counter()
            request = asyncio.create_task(
                asyncio.to_thread(
                    mcp.request,
                    "tools/call",
                    {
                        "name": "kb_add",
                        "arguments": {"type": "decision", "title": "Measured", "body": "data", "source": "test"},
                    },
                )
            )
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                wakeup = time.perf_counter() - before
                _measure("loop_during_mcp", wakeup)
                response, _ = await _daemon_rpc(core.reader, core.writer, 3, "app/health", EmptyParams())
                assert response.result is not None
                assert wakeup < _LIMIT_S
            finally:
                release.set()
                result = await request
                assert not result.get("isError", False)
                assert _content(result)["kb_id"]

    asyncio.run(scenario())


def test_permission_policy_tracks_public_settings_updates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _running(tmp_path, monkeypatch) as core:
            current = await core.client.call("settings/get", EmptyParams(), Settings)
            native = await core.client.call(
                "settings/update",
                SettingsUpdateParams(
                    rev=current.rev,
                    patch=SettingsPatch(approvals_mode="native"),
                ),
                Settings,
            )
            hook = await _permission(core)
            status, body = await asyncio.wait_for(hook, 2)
            assert status == 200
            assert msgspec.json.decode(body) == {}
            await core.client.call(
                "settings/update",
                SettingsUpdateParams(
                    rev=native.rev,
                    patch=SettingsPatch(approvals_mode="inline"),
                ),
                Settings,
            )
            hook = await _permission(core)
            await _decide(core, 3)
            assert (await asyncio.wait_for(hook, 2))[0] == 200

    asyncio.run(scenario())


def test_permission_does_not_wait_for_setup_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _running(tmp_path, monkeypatch) as core:
            entered, release = threading.Event(), threading.Event()

            def delayed_checks(_self: SetupService) -> ChecksResult:
                entered.set()
                _ = release.wait(_STALL_S)
                return ChecksResult(checks=())

            monkeypatch.setattr(SetupService, "checks", delayed_checks)
            checking = asyncio.create_task(core.client.call("setup/checks", EmptyParams(), ChecksResult))
            hook = None
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                before = time.perf_counter()
                hook = await _permission(core)
                await _decide(core, 3)
                assert (await asyncio.wait_for(hook, 2))[0] == 200
                elapsed = time.perf_counter() - before
                _measure("approval_during_setup", elapsed)
                assert elapsed < _LIMIT_S
            finally:
                release.set()
                await checking
                if hook is not None:
                    await asyncio.gather(hook, return_exceptions=True)

    asyncio.run(scenario())
