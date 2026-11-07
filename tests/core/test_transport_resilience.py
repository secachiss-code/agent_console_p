"""Recoverable IPC failures and bounded ingest loss are visible to a live client."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import msgspec
import pytest

from aconsole.contracts.app import EmptyParams, ProjectAddParams, ProjectView, TabIdParams, TabView, TerminalLaunchSpec
from aconsole.contracts.events import Notify
from aconsole.contracts.ipc import ResponseErr
from aconsole.core.application.services.setup import SetupService
from aconsole.core.application.services.tabs import TabService
from aconsole.core.infrastructure.http.routes import IngestItem, IngestQueue
from aconsole.core.infrastructure.ipc.framing import decode_envelope
from aconsole.ui.client.core_client import CoreClient
from tests.core.test_review_wire import _hook
from tests.core.test_wire import _connect, _daemon, _git_repo, _open, _stop

if TYPE_CHECKING:
    from pathlib import Path

    from aconsole.contracts.app import AppSnapshot, ChecksResult
    from aconsole.core.bootstrap import Daemon


def test_bad_json_gets_an_error_and_the_same_socket_remains_usable(tmp_path: Path) -> None:
    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        serving = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            client.writer.write(b"{not json}\n")
            await client.writer.drain()
            line = await asyncio.wait_for(client.reader.readline(), 1)
            assert line, "recoverable malformed frame closed the socket"
            error = decode_envelope(line)
            assert isinstance(error, ResponseErr)
            assert error.error.message == "PARSE_ERROR"
            assert error.id is None
            assert (await client.snapshot()).api_version
        finally:
            await _stop(daemon, serving, client)

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["setup/checks", "app/snapshot"])
def test_turn_handler_crash_answers_once_and_keeps_processing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    def broken_checks(_self: SetupService) -> ChecksResult:
        message = "private failure detail must not reach the client"
        raise RuntimeError(message)

    def broken_snapshot(_self: TabService) -> AppSnapshot:
        message = "private failure detail must not reach the client"
        raise RuntimeError(message)

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        serving = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            with monkeypatch.context() as patch:
                if method == "setup/checks":
                    patch.setattr(SetupService, "checks", broken_checks)
                else:
                    patch.setattr(TabService, "snapshot", broken_snapshot)
                result = await asyncio.wait_for(client.raw(method, EmptyParams()), 1)
                assert isinstance(result, ResponseErr)
                assert result.error.message == "INTERNAL_ERROR"
                assert "private failure" not in msgspec.json.encode(result).decode()
            assert (await client.snapshot()).api_version
        finally:
            await _stop(daemon, serving, client)

    asyncio.run(scenario())


def test_the_last_oversized_event_triggers_automatic_resync_without_reconnect(tmp_path: Path) -> None:
    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        daemon._frame_limit = 16 * 1024
        serving = asyncio.create_task(daemon.serve())
        ipc = await _connect(daemon.socket)
        client = CoreClient(str(daemon.socket))
        try:
            _ = await client.connect()
            _ = await client.pull_snapshot()
            daemon._approval_event(
                Notify(
                    tab_id=None, title="large", body="x" * 32768, priority="normal", episode_id="oversized-last-event"
                )
            )
            for _attempt in range(150):
                if client.resync_count > 0 and not client.store.reconnecting:
                    break
                await asyncio.sleep(0.01)
            assert client.resync_count > 0, "the final dropped event gave the UI no reason to resync"
            assert client.store.seq == daemon._ring.current_seq
            assert client.connects == 1
            assert not client.store.reconnecting
        finally:
            await client.close()
            await _stop(daemon, serving, ipc)

    asyncio.run(scenario())


def _item(tab: str, body: bytes) -> IngestItem:
    return IngestItem(tab_id=tab, kind="claude.hook", body=body)


def test_a_new_item_cannot_jump_ahead_of_older_overflow_for_the_same_tab() -> None:
    queue = IngestQueue(limit=1)
    first, old, new = _item("first", b"first"), _item("tab", b"old"), _item("tab", b"new")
    queue.push(first)
    queue.push(old)
    assert queue.pop() == first
    queue.push(new)
    assert queue.pop() == new
    assert queue.pop() is None, "an old overflow item was applied after its newer replacement"


def test_coalesced_tabs_follow_the_order_of_their_latest_updates() -> None:
    queue = IngestQueue(limit=1)
    first, a1, b1, a2 = _item("first", b"first"), _item("a", b"1"), _item("b", b"1"), _item("a", b"2")
    for item in (first, a1, b1, a2):
        queue.push(item)
    assert queue.pop() == first
    assert queue.pop() == b1
    assert queue.pop() == a2


def test_ingest_overflow_is_reported_after_latest_state_and_resyncs_the_ui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FAKE_HOME", str(home))
    repo = _git_repo(tmp_path / "repo")

    async def scenario() -> None:
        from aconsole.core.bootstrap import Daemon

        entered, release = asyncio.Event(), asyncio.Event()
        applied = []
        original = Daemon._apply_hook

        async def delayed(self: Daemon, tab_id: str, event: str) -> None:
            if not entered.is_set():
                entered.set()
                await asyncio.wait_for(release.wait(), 5)
            applied.append(event)
            await original(self, tab_id, event)

        monkeypatch.setattr(Daemon, "_apply_hook", delayed)
        daemon = _daemon(tmp_path)
        daemon._ingest_queue = IngestQueue(limit=1)
        serving = asyncio.create_task(daemon.serve())
        ipc = await _connect(daemon.socket)
        client = CoreClient(str(daemon.socket))
        try:
            project = await ipc.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            tab = await ipc.call("tab/open", _open(project.id), TabView)
            spec = await ipc.call("tab/launchSpec", TabIdParams(tab_id=tab.id), TerminalLaunchSpec)
            _ = await client.connect()
            _ = await client.pull_snapshot()
            port = daemon.ingest_port
            assert port is not None

            async def post(name: str) -> None:
                await asyncio.to_thread(
                    _hook,
                    port,
                    str(tab.id),
                    spec.env["ACONSOLE_TAB_TOKEN"],
                    {
                        "hook_event_name": name,
                        "session_id": "s",
                        "tool_name": "Bash",
                        "stop_hook_active": False,
                    },
                )

            await post("PreToolUse")
            await asyncio.wait_for(entered.wait(), 2)
            for name in ("PostToolUse", "PreToolUse", "Stop"):
                await post(name)
            assert daemon._ingest_queue.needs_resync(str(tab.id))
            release.set()
            for _attempt in range(200):
                if client.resync_count > 0 and not client.store.reconnecting:
                    break
                await asyncio.sleep(0.01)
            assert applied == ["PreToolUse", "PostToolUse", "Stop"]
            assert client.resync_count > 0, "ingest loss was cleared without notifying the UI"
            assert not client.store.reconnecting
            assert client.connects == 1
        finally:
            release.set()
            await client.close()
            await _stop(daemon, serving, ipc)

    asyncio.run(scenario())


def test_framed_oversize_error_can_be_followed_by_a_valid_request(tmp_path: Path) -> None:
    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        daemon._frame_limit = 16 * 1024
        serving = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            client.writer.write(b'{"pad":"' + b"x" * 20000 + b'"}\n')
            await client.writer.drain()
            error = decode_envelope(await asyncio.wait_for(client.reader.readline(), 1))
            assert isinstance(error, ResponseErr)
            assert error.error.message == "MESSAGE_TOO_LARGE"
            assert (await client.snapshot()).api_version
        finally:
            await _stop(daemon, serving, client)

    asyncio.run(scenario())


def test_ingest_apply_failure_reports_loss_and_keeps_consuming() -> None:
    from aconsole.core.infrastructure.http.consumer import IngestHandlers, consume

    async def scenario() -> None:
        queue = IngestQueue(limit=3)
        for name in ("PreToolUse", "Stop"):
            queue.push(_item("tab", msgspec.json.encode({"hook_event_name": name, "session_id": "s"})))
        ready = asyncio.Event()
        ready.set()
        applied, losses = [], []

        async def apply(_tab: str, name: str) -> None:
            applied.append(name)
            if name == "PreToolUse":
                message = "injected handler failure"
                raise RuntimeError(message)

        async def lost(tab: str, count: int) -> None:
            losses.append((tab, count))

        consumer = asyncio.create_task(consume(queue, ready, apply, IngestHandlers(on_loss=lost)))
        try:
            for _attempt in range(100):
                if len(applied) == 2:
                    break
                await asyncio.sleep(0.001)
            assert applied == ["PreToolUse", "Stop"]
            assert losses == [("tab", 1)]
        finally:
            _ = consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
    asyncio.run(scenario())
