"""Approval identities and close cleanup stay within one tab."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING

import msgspec
import pytest

from aconsole.contracts.app import (
    CloseTabParams,
    DecideParams,
    EmptyParams,
    ProjectAddParams,
    ProjectView,
    SnapshotResult,
    SubscribeParams,
    TabIdParams,
    TabView,
    TerminalLaunchSpec,
)
from aconsole.contracts.common import ApprovalDecision as ContractDecision
from aconsole.contracts.common import ClientRole
from aconsole.contracts.events import ApprovalRequested
from aconsole.contracts.ingest import HookResponse
from aconsole.contracts.ipc import InitializeParams, ProtocolVersion
from aconsole.core.domain.approvals import ApprovalBook, ApprovalDecision, ApprovalPhase
from aconsole.core.domain.errors import DomainError
from tests.core.domain.test_approvals import AT, _request
from tests.core.test_approval_hold import _daemon_event, _daemon_rpc, _post_daemon_permission_hook
from tests.core.test_wire import _connect, _daemon, _git_repo, _open, _stop

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("event_id", ["", "tool-1"])
@pytest.mark.parametrize("decision", [None, ApprovalDecision.ALLOW, ApprovalDecision.DENY])
def test_other_tab_cannot_share_pending_or_settled_approval(event_id: str, decision: ApprovalDecision | None) -> None:
    book = ApprovalBook()
    request = replace(_request(), event_id=event_id)
    original = book.open(request)
    if decision is not None:
        original = book.decide(original.id, nonce=original.nonce, decision=decision, remember_prefix=False, at=AT)
    other = book.open(replace(request, approval_id="a2", nonce="n2", tab_id="tab-other"))
    assert other.id == "a2"
    assert other.tab_id == "tab-other"
    assert other.phase is ApprovalPhase.PENDING
    assert book.get(original.id) == original


@pytest.mark.parametrize("changed", ["session", "tool", "input"])
def test_event_id_cannot_replay_a_different_context(changed: str) -> None:
    book = ApprovalBook()
    request = replace(_request(), event_id="tool-1")
    first = book.open(request)
    book.decide(first.id, nonce=first.nonce, decision=ApprovalDecision.ALLOW, remember_prefix=False, at=AT)
    second = replace(request, approval_id="a2", nonce="n2")
    if changed == "session":
        second = replace(second, session_id="other-session")
    elif changed == "tool":
        second = replace(second, tool="Write")
    else:
        second = replace(second, input_sha256="other-input", command="git diff")
    opened = book.open(second)
    assert opened.id == "a2"
    assert opened.phase is ApprovalPhase.PENDING


def test_dropping_one_tab_keeps_other_pending_and_replay_identity() -> None:
    book = ApprovalBook()
    first = book.open(replace(_request(), event_id="tool-1"))
    other_request = replace(_request(), approval_id="a2", nonce="n2", tab_id="other", session_id="other-session",
                            event_id="other-event")
    second = book.open(other_request)
    released = book.drop_tab(first.tab_id)
    assert [item.id for item in released] == [first.id]
    assert released[0].phase is ApprovalPhase.CANCELLED
    with pytest.raises(DomainError, match="APPROVAL_NOT_FOUND"):
        book.get(first.id)
    replay = book.open(replace(other_request, approval_id="a3", nonce="n3"))
    assert replay.id == second.id
    assert replay.phase is ApprovalPhase.PENDING


def test_public_tab_close_releases_only_its_held_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FAKE_HOME", str(home))
    repos = [_git_repo(tmp_path / f"repo-{index}") for index in range(2)]

    async def scenario() -> None:
        daemon = _daemon(tmp_path)
        serving = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        writer: asyncio.StreamWriter | None = None
        hooks: list[asyncio.Task[tuple[int, bytes]]] = []
        try:
            projects = [
                await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView) for repo in repos
            ]
            tabs = [await client.call("tab/open", _open(project.id), TabView) for project in projects]
            specs = [
                await client.call("tab/launchSpec", TabIdParams(tab_id=tab.id), TerminalLaunchSpec) for tab in tabs
            ]
            reader, writer = await asyncio.open_unix_connection(daemon.socket)
            await _daemon_rpc(reader, writer, 1, "initialize", InitializeParams(
                protocol=(ProtocolVersion(major=1, minor=0), ProtocolVersion(major=1, minor=0)),
                role=ClientRole.UI, client_name="tab-isolation", client_version="1", pid=1,
                capabilities=frozenset({"events.v1", "approvals.inline"}),
            ))
            await _daemon_rpc(reader, writer, 2, "events/subscribe", SubscribeParams(after_seq=0))
            assert daemon.ingest_port is not None
            approvals = []
            for index, (tab, spec) in enumerate(zip(tabs, specs, strict=True)):
                body = msgspec.json.encode({"session_id": f"session-{index}", "hook_event_name": "PermissionRequest",
                    "tool_name": "Bash", "tool_input": {"command": "git status"}})
                hook = asyncio.create_task(asyncio.to_thread(_post_daemon_permission_hook, daemon.ingest_port,
                    spec.env["ACONSOLE_TAB_TOKEN"], str(tab.id), body))
                hooks.append(hook)
                event, _earlier = await _daemon_event(reader)
                assert isinstance(event.event, ApprovalRequested)
                approvals.append(event.event.approval)
            await _daemon_rpc(reader, writer, 3, "tab/close", CloseTabParams(tab_id=tabs[0].id))
            status, body = await asyncio.wait_for(asyncio.shield(hooks[0]), 1)
            assert status == 200
            assert msgspec.json.decode(body) == {}
            assert not hooks[1].done()
            response, _ = await _daemon_rpc(reader, writer, 4, "app/snapshot", EmptyParams())
            snapshot = msgspec.json.decode(response.result, type=SnapshotResult).snapshot
            assert {approval.id for approval in snapshot.approvals} == {approvals[1].id}
            await _daemon_rpc(reader, writer, 5, "approval/decide", DecideParams(
                approval_id=approvals[1].id, nonce=approvals[1].nonce, decision=ContractDecision.DENY,
            ))
            status, body = await asyncio.wait_for(hooks[1], 2)
            assert status == 200
            assert msgspec.json.decode(body, type=HookResponse).hook_specific_output.decision.behavior == "deny"
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            await _stop(daemon, serving, client)
            if hooks:
                await asyncio.gather(*hooks, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("decision", [ApprovalDecision.ALLOW, ApprovalDecision.DENY])
def test_close_forgets_settled_decisions_and_rules_only_for_that_tab(decision: ApprovalDecision) -> None:
    book = ApprovalBook()
    first_request = replace(_request(), event_id="event-a")
    first = book.open(first_request)
    book.decide(first.id, nonce=first.nonce, decision=decision, remember_prefix=True, at=AT)
    second_request = replace(first_request, approval_id="a2", nonce="n2", tab_id="other", event_id="event-b")
    second = book.open(second_request)
    book.decide(second.id, nonce=second.nonce, decision=decision, remember_prefix=True, at=AT)
    book.drop_tab(first.tab_id)
    with pytest.raises(DomainError, match="APPROVAL_NOT_FOUND"):
        book.get(first.id)
    fresh = book.open(replace(first_request, approval_id="a3", nonce="n3"))
    assert fresh.id == "a3"
    assert fresh.phase is ApprovalPhase.PENDING
    replay = book.open(replace(second_request, approval_id="a4", nonce="n4"))
    assert replay.id == second.id
    expected = ApprovalPhase.ALLOWED if decision is ApprovalDecision.ALLOW else ApprovalPhase.DENIED
    assert replay.phase is expected
