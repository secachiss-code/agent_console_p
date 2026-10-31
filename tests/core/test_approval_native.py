"""Native-mode and UI-loss fallback release held permission hooks exactly once."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from typing import TYPE_CHECKING

import msgspec
import pytest
from sqlalchemy.orm import Session

from aconsole.contracts.app import (
    ApprovalView,
    CommandDetail,
    DecideParams,
    EmptyParams,
    ReleasedResult,
    ReleaseParams,
    SettingsUpdateParams,
    SubscribeParams,
)
from aconsole.contracts.common import (
    AgentKind,
    ApprovalId,
    ApprovalScope,
    ClientRole,
    TabId,
    TabStatus,
    WorkspaceMode,
)
from aconsole.contracts.common import ApprovalDecision as ContractDecision
from aconsole.contracts.events import ApprovalRequested, ApprovalResolved, Notify
from aconsole.contracts.ipc import (
    EventEnvelope,
    InitializeParams,
    InitializeResult,
    Notification,
    ProtocolVersion,
    Request,
    ResponseErr,
    ResponseOk,
)
from aconsole.contracts.settings import Settings, SettingsPatch
from aconsole.core.application.services.approval_broker import ApprovalHold, HookOutcome
from aconsole.core.bootstrap import Daemon
from aconsole.core.domain.approvals import Approval, ApprovalBook, ApprovalPhase, OpenRequest
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.ipc.framing import decode_envelope, encode_line
from aconsole.core.infrastructure.persistence.engine import write_engine
from aconsole.core.infrastructure.persistence.models import ApprovalStatus, Project, Tab, Workspace
from aconsole.core.infrastructure.persistence.repositories.catalog import ApprovalRepository
from aconsole.core.infrastructure.persistence.startup import prepare_database, sqlite_url
from tests.core.conftest import wait_for_socket

if TYPE_CHECKING:
    from pathlib import Path

_HOLD = timedelta(seconds=60)
_ENCODER = msgspec.json.Encoder()
_CAPABLE = frozenset({"events.v1", "approvals.inline", "notifications.desktop"})


class _Harness:
    def __init__(self) -> None:
        self.now_at = datetime.now(UTC)
        self.events: list[ApprovalRequested | ApprovalResolved] = []
        self.notifications: list[Approval] = []
        self.audit: list[Approval] = []
        self.requested = asyncio.Event()
        self.service = ApprovalHold(
            ApprovalBook(),
            publish=self._publish,
            notify=self.notifications.append,
            audit=self.audit.append,
            now=self._now,
        )

    def _now(self) -> datetime:
        return self.now_at

    def _publish(self, event: ApprovalRequested | ApprovalResolved) -> None:
        self.events.append(event)
        if isinstance(event, ApprovalRequested):
            self.requested.set()


def _request(
    at: datetime,
    *,
    approval_id: str = "approval-native",
    nonce: str = "nonce-native",
    expires_at: datetime | None = None,
    session_id: str = "session-native",
) -> OpenRequest:
    return OpenRequest(
        approval_id=approval_id,
        nonce=nonce,
        tab_id="tab-native",
        session_id=session_id,
        tool="Bash",
        command="git status",
        input_sha256=f"sha256:{session_id}:git status",
        risk=frozenset(),
        at=at,
        expires_at=expires_at if expires_at is not None else at + _HOLD,
        allow_tab_session=True,
    )


def _view(request: OpenRequest) -> ApprovalView:
    return ApprovalView(
        id=ApprovalId(request.approval_id),
        nonce=request.nonce,
        tab_id=TabId(request.tab_id),
        agent=AgentKind.CLAUDE,
        tool=request.tool,
        summary=request.command,
        detail=CommandDetail(command=request.command, args_preview=None),
        cwd="/worktree",
        risk=frozenset(),
        requested_at=request.at,
        expires_at=request.expires_at,
        allow_tab_session=request.allow_tab_session,
    )


def _decide(
    request: OpenRequest,
    *,
    decision: ContractDecision,
    nonce: str | None = None,
) -> DecideParams:
    return DecideParams(
        approval_id=ApprovalId(request.approval_id),
        nonce=request.nonce if nonce is None else nonce,
        decision=decision,
        scope=ApprovalScope.ONCE,
        message="Denied from test UI" if decision is ContractDecision.DENY else None,
    )


def _release(request: OpenRequest, *, nonce: str | None = None) -> ReleaseParams:
    return ReleaseParams(approval_id=ApprovalId(request.approval_id), nonce=request.nonce if nonce is None else nonce)


def test_release_service_nonce_validation_has_one_terminal_settlement() -> None:
    asyncio.run(_release_service_nonce_validation_has_one_terminal_settlement())


async def _release_service_nonce_validation_has_one_terminal_settlement() -> None:
    harness = _Harness()
    request = _request(harness.now_at)
    waiter = asyncio.create_task(harness.service.hold(request, _view(request)))
    await harness.requested.wait()

    with pytest.raises(DomainError, match="NONCE_MISMATCH"):
        _ = harness.service.release_to_native(_release(request, nonce="wrong"), at=harness.now_at)
    assert not waiter.done()
    assert len(harness.audit) == 0

    released = harness.service.release_to_native(_release(request), at=harness.now_at)
    assert released == ReleasedResult(released=True)
    assert await waiter == HookOutcome(phase=ApprovalPhase.EXPIRED_TO_NATIVE, message=None)
    assert len(harness.events) == 2
    assert isinstance(harness.events[1], ApprovalResolved)
    assert harness.events[1].outcome == "expired_to_native"
    assert harness.events[1].by == "user"
    assert len(harness.audit) == 1
    assert harness.audit[0].phase is ApprovalPhase.EXPIRED_TO_NATIVE

    with pytest.raises(DomainError, match="ALREADY_DECIDED"):
        _ = harness.service.decide(_decide(request, decision=ContractDecision.DENY), at=harness.now_at)
    with pytest.raises(DomainError, match="ALREADY_DECIDED"):
        _ = harness.service.release_to_native(_release(request), at=harness.now_at)
    harness.service.expire(at=request.expires_at + _HOLD)
    assert len(harness.events) == 2
    assert len(harness.audit) == 1


def test_permission_hook_native_mode_and_missing_ui_return_empty_response(tmp_path: Path) -> None:
    asyncio.run(_permission_hook_native_mode_and_missing_ui_return_empty_response(tmp_path))


async def _permission_hook_native_mode_and_missing_ui_return_empty_response(tmp_path: Path) -> None:
    daemon, tab_id, serving = await _start_daemon(tmp_path)
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None
    try:
        before_seq = daemon._ring.current_seq
        status, body = await asyncio.wait_for(_permission_hook(daemon, tab_id), 5)
        assert status == 200
        assert body == b"{}"
        assert daemon.approvals.pending_views() == ()
        assert daemon._ring.current_seq == before_seq

        reader, writer = await _open_ui(daemon)
        settings_response, _ = await _daemon_rpc(reader, writer, 3, "settings/get", EmptyParams())
        settings = msgspec.json.decode(settings_response.result, type=Settings)
        updated, _ = await _daemon_rpc(
            reader,
            writer,
            4,
            "settings/update",
            SettingsUpdateParams(
                patch=SettingsPatch(approvals_mode="native"),
                rev=settings.rev,
            ),
        )
        assert updated.id == 4

        before_seq = daemon._ring.current_seq
        status, body = await asyncio.wait_for(_permission_hook(daemon, tab_id), 5)
        assert status == 200
        assert body == b"{}"
        assert daemon.approvals.pending_views() == ()
        assert daemon._ring.current_seq == before_seq
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        daemon.request_stop()
        await asyncio.wait_for(serving, 15)


def test_daemon_release_to_native_is_single_hook_reply_and_audit(tmp_path: Path) -> None:
    asyncio.run(_daemon_release_to_native_is_single_hook_reply_and_audit(tmp_path))


async def _daemon_release_to_native_is_single_hook_reply_and_audit(tmp_path: Path) -> None:
    daemon, tab_id, serving = await _start_daemon(tmp_path)
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None
    hook: asyncio.Task[tuple[int, bytes]] | None = None
    try:
        reader, writer = await _open_ui(daemon)
        hook = _start_permission_hook(daemon, tab_id)
        approval_event, _ = await _daemon_event(reader, ApprovalRequested)
        assert isinstance(approval_event.event, ApprovalRequested)
        approval = approval_event.event.approval
        assert not hook.done()

        bad_nonce, _ = await _daemon_rpc_any(
            reader,
            writer,
            3,
            "approval/releaseToNative",
            ReleaseParams(approval_id=approval.id, nonce="wrong"),
        )
        assert isinstance(bad_nonce, ResponseErr)
        assert bad_nonce.error.message == "APPROVAL_NONCE_MISMATCH"
        assert not hook.done()

        released, release_events = await _daemon_rpc(
            reader,
            writer,
            4,
            "approval/releaseToNative",
            ReleaseParams(approval_id=approval.id, nonce=approval.nonce),
        )
        assert msgspec.json.decode(released.result, type=ReleasedResult).released
        resolved = tuple(
            item.event
            for item in release_events
            if isinstance(item.event, ApprovalResolved) and item.event.approval_id == approval.id
        )
        assert len(resolved) == 1
        assert resolved[0].outcome == "expired_to_native"
        assert resolved[0].by == "user"

        denied, _ = await _daemon_rpc_any(
            reader,
            writer,
            5,
            "approval/decide",
            DecideParams(approval_id=approval.id, nonce=approval.nonce, decision=ContractDecision.DENY),
        )
        assert isinstance(denied, ResponseErr)
        assert denied.error.message == "APPROVAL_ALREADY_DECIDED"
        released_again, _ = await _daemon_rpc_any(
            reader,
            writer,
            6,
            "approval/releaseToNative",
            ReleaseParams(approval_id=approval.id, nonce=approval.nonce),
        )
        assert isinstance(released_again, ResponseErr)
        assert released_again.error.message == "APPROVAL_ALREADY_DECIDED"

        status, body = await asyncio.wait_for(hook, 5)
        assert status == 200
        assert body == b"{}"
        assert daemon.approvals.pending_views() == ()
        assert len(_events_for(daemon, approval.id)) == 1
        ring_events = daemon._ring.since(0)
        assert ring_events is not None
        assert sum(
            isinstance(item.event, Notify) and item.event.episode_id == str(approval.id)
            for item in ring_events
        ) == 1
        assert daemon._writer is not None
        audit = await asyncio.wait_for(
            daemon._writer.submit(lambda session: ApprovalRepository().one(session, str(approval.id))),
            5,
        )
        assert audit.status is ApprovalStatus.EXPIRED_TO_NATIVE
        assert audit.decided_by == "user"
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        daemon.request_stop()
        await asyncio.wait_for(serving, 15)


def test_last_capable_ui_disconnect_releases_only_after_all_capable_uis_leave(tmp_path: Path) -> None:
    asyncio.run(_last_capable_ui_disconnect_releases_only_after_all_capable_uis_leave(tmp_path))


async def _last_capable_ui_disconnect_releases_only_after_all_capable_uis_leave(tmp_path: Path) -> None:
    daemon, tab_id, serving = await _start_daemon(tmp_path)
    first_writer: asyncio.StreamWriter | None = None
    second_writer: asyncio.StreamWriter | None = None
    hook: asyncio.Task[tuple[int, bytes]] | None = None
    try:
        first_reader, first_writer = await _open_ui(daemon, request_base=1)
        _, second_writer = await _open_ui(daemon, request_base=1)
        await _wait_for_capable_ui_count(daemon, 2)
        hook = _start_permission_hook(daemon, tab_id)
        approval_event, _ = await _daemon_event(first_reader, ApprovalRequested)
        assert isinstance(approval_event.event, ApprovalRequested)
        approval = approval_event.event.approval
        assert not hook.done()

        first_writer.close()
        await first_writer.wait_closed()
        first_writer = None
        await _wait_for_capable_ui_count(daemon, 1)
        assert not hook.done(), "one remaining capable UI must keep the hook pending"
        assert len(daemon.approvals.pending_views()) == 1

        second_writer.close()
        await second_writer.wait_closed()
        second_writer = None
        await _wait_for_capable_ui_count(daemon, 0)
        status, body = await asyncio.wait_for(hook, 5)
        assert status == 200
        assert body == b"{}"
        assert daemon.approvals.pending_views() == ()
        resolved = _events_for(daemon, approval.id)
        assert len(resolved) == 1
        assert resolved[0].outcome == "expired_to_native"
        # Losing the inline UI ends the response window; only timeout/user/rule are audit sources.
        assert resolved[0].by == "timeout"
        ring_events = daemon._ring.since(0)
        assert ring_events is not None
        assert sum(
            isinstance(item.event, Notify) and item.event.episode_id == str(approval.id)
            for item in ring_events
        ) == 1
        assert daemon._writer is not None
        audit = await asyncio.wait_for(
            daemon._writer.submit(lambda session: ApprovalRepository().one(session, str(approval.id))),
            5,
        )
        assert audit.status is ApprovalStatus.EXPIRED_TO_NATIVE
        assert audit.decided_by == "timeout"
    finally:
        for writer in (first_writer, second_writer):
            if writer is not None:
                writer.close()
                await writer.wait_closed()
        daemon.request_stop()
        await asyncio.wait_for(serving, 15)


def test_permission_timeout_returns_native_response_not_allow(tmp_path: Path) -> None:
    asyncio.run(_permission_timeout_returns_native_response_not_allow(tmp_path))


async def _permission_timeout_returns_native_response_not_allow(tmp_path: Path) -> None:
    daemon, tab_id, serving = await _start_daemon(tmp_path)
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None
    hook: asyncio.Task[tuple[int, bytes]] | None = None
    try:
        reader, writer = await _open_ui(daemon)
        hook = _start_permission_hook(daemon, tab_id)
        approval_event, _ = await _daemon_event(reader, ApprovalRequested)
        assert isinstance(approval_event.event, ApprovalRequested)
        approval = approval_event.event.approval
        assert not hook.done()

        daemon.approvals.expire(at=approval.expires_at)
        status, body = await asyncio.wait_for(hook, 5)
        assert status == 200
        assert body == b"{}"
        resolved = _events_for(daemon, approval.id)
        assert len(resolved) == 1
        assert resolved[0].outcome == "expired_to_native"
        assert resolved[0].by == "timeout"
        assert daemon._writer is not None
        audit = await asyncio.wait_for(
            daemon._writer.submit(lambda session: ApprovalRepository().one(session, str(approval.id))),
            5,
        )
        assert audit.status is ApprovalStatus.EXPIRED_TO_NATIVE
        assert audit.decided_by == "timeout"
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        daemon.request_stop()
        await asyncio.wait_for(serving, 15)


def test_daemon_shutdown_releases_held_hook_before_ingest_teardown(tmp_path: Path) -> None:
    asyncio.run(_daemon_shutdown_releases_held_hook_before_ingest_teardown(tmp_path))


async def _daemon_shutdown_releases_held_hook_before_ingest_teardown(tmp_path: Path) -> None:
    daemon, tab_id, serving = await _start_daemon(tmp_path)
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None
    hook: asyncio.Task[tuple[int, bytes]] | None = None
    try:
        reader, writer = await _open_ui(daemon)
        hook = _start_permission_hook(daemon, tab_id)
        approval_event, _ = await _daemon_event(reader, ApprovalRequested)
        assert isinstance(approval_event.event, ApprovalRequested)
        approval = approval_event.event.approval
        assert not hook.done()

        # Shutdown ends the inline response opportunity; INGEST §4.2.8 has no "agent" audit source.
        daemon.request_stop()
        status, body = await asyncio.wait_for(hook, 5)
        assert status == 200
        assert body == b"{}"
        assert daemon.approvals.pending_views() == ()
        resolved = _events_for(daemon, approval.id)
        assert len(resolved) == 1
        assert resolved[0].outcome == "expired_to_native"
        assert resolved[0].by == "timeout"
        ring_events = daemon._ring.since(0)
        assert ring_events is not None
        assert sum(
            isinstance(item.event, Notify) and item.event.episode_id == str(approval.id)
            for item in ring_events
        ) == 1
        assert daemon._writer is not None
        audit = await asyncio.wait_for(
            daemon._writer.submit(lambda session: ApprovalRepository().one(session, str(approval.id))),
            5,
        )
        assert audit.status is ApprovalStatus.EXPIRED_TO_NATIVE
        assert audit.decided_by == "timeout"
        await asyncio.wait_for(serving, 15)
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        daemon.request_stop()
        await asyncio.wait_for(serving, 15)


async def _start_daemon(tmp_path: Path) -> tuple[Daemon, str, asyncio.Task[int]]:
    database = tmp_path / "aconsole.db"
    tab_id = "01JBF5SW7QNZ2X5QR8M6K9H000"
    _seed_daemon_tab(database, tmp_path, tab_id)
    daemon = Daemon(tmp_path / "core.sock", idle_s=30, database=database)
    daemon.serve_ingest = True
    daemon._tokens.register(tab_id, "test-tab-token")
    serving = asyncio.create_task(daemon.serve())
    await wait_for_socket(daemon.socket)
    return daemon, tab_id, serving


async def _open_ui(
    daemon: Daemon,
    *,
    capabilities: frozenset[str] = _CAPABLE,
    request_base: int = 1,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_unix_connection(daemon.socket)
    initialized, _ = await _daemon_rpc(
        reader,
        writer,
        request_base,
        "initialize",
        InitializeParams(
            protocol=(ProtocolVersion(major=1, minor=0), ProtocolVersion(major=1, minor=0)),
            role=ClientRole.UI,
            client_name="approval-native-test",
            client_version="0.0.0",
            pid=os.getpid(),
            capabilities=capabilities,
        ),
    )
    result = msgspec.json.decode(initialized.result, type=InitializeResult)
    assert "approvals.inline" in result.capabilities
    _, _ = await _daemon_rpc(
        reader,
        writer,
        request_base + 1,
        "events/subscribe",
        SubscribeParams(after_seq=0),
    )
    return reader, writer


async def _permission_hook(daemon: Daemon, tab_id: str) -> tuple[int, bytes]:
    assert daemon.ingest_port is not None
    return await asyncio.to_thread(
        _post_daemon_permission_hook,
        daemon.ingest_port,
        "test-tab-token",
        tab_id,
        b'{"session_id":"native-session","hook_event_name":"PermissionRequest",'
        b'"tool_name":"Bash","tool_input":{"command":"git status"}}',
    )


def _start_permission_hook(daemon: Daemon, tab_id: str) -> asyncio.Task[tuple[int, bytes]]:
    return asyncio.create_task(_permission_hook(daemon, tab_id))


async def _daemon_rpc(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request_id: int,
    method: str,
    params: msgspec.Struct,
) -> tuple[ResponseOk, tuple[EventEnvelope, ...]]:
    response, events = await _daemon_rpc_any(reader, writer, request_id, method, params)
    if isinstance(response, ResponseErr):
        pytest.fail(f"{method} failed: {response.error.message}")
    return response, events


async def _daemon_rpc_any(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request_id: int,
    method: str,
    params: msgspec.Struct,
) -> tuple[ResponseOk | ResponseErr, tuple[EventEnvelope, ...]]:
    request = Request(id=request_id, method=method, params=msgspec.Raw(_ENCODER.encode(params)))
    writer.write(encode_line(_ENCODER, request))
    await writer.drain()
    events: list[EventEnvelope] = []
    while True:
        envelope = decode_envelope(await asyncio.wait_for(reader.readuntil(b"\n"), 5))
        if isinstance(envelope, Notification) and envelope.method == "$/event":
            events.append(msgspec.json.decode(envelope.params, type=EventEnvelope))
            continue
        if isinstance(envelope, ResponseErr):
            if envelope.id != request_id:
                pytest.fail(f"{method} returned id {envelope.id}, expected {request_id}")
            return envelope, tuple(events)
        if isinstance(envelope, ResponseOk):
            if envelope.id != request_id:
                pytest.fail(f"{method} returned id {envelope.id}, expected {request_id}")
            return envelope, tuple(events)
        pytest.fail(f"{method} returned an unexpected IPC envelope")


async def _daemon_event(
    reader: asyncio.StreamReader,
    event_type: type[ApprovalRequested | ApprovalResolved],
) -> tuple[EventEnvelope, tuple[EventEnvelope, ...]]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    skipped: list[EventEnvelope] = []
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            pytest.fail(f"daemon did not publish {event_type.__name__}")
        envelope = decode_envelope(await asyncio.wait_for(reader.readuntil(b"\n"), remaining))
        if not isinstance(envelope, Notification) or envelope.method != "$/event":
            pytest.fail("daemon sent a non-event frame while waiting for approval event")
        event = msgspec.json.decode(envelope.params, type=EventEnvelope)
        if isinstance(event.event, event_type):
            return event, tuple(skipped)
        skipped.append(event)


async def _wait_for_capable_ui_count(daemon: Daemon, expected: int) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while True:
        count = sum(
            1
            for connection in daemon._connections
            if connection.ready
            and connection.role is ClientRole.UI
            and "approvals.inline" in connection.capabilities
        )
        if count == expected:
            return
        remaining = deadline - loop.time()
        if remaining <= 0:
            pytest.fail(f"expected {expected} capable UIs, found {count}")
        await asyncio.sleep(min(0.01, remaining))


def _events_for(daemon: Daemon, approval_id: ApprovalId) -> tuple[ApprovalResolved, ...]:
    envelopes = daemon._ring.since(0)
    assert envelopes is not None
    return tuple(
        event
        for envelope in envelopes
        if isinstance((event := envelope.event), ApprovalResolved) and event.approval_id == approval_id
    )


def _post_daemon_permission_hook(port: int, token: str, tab_id: str, body: bytes) -> tuple[int, bytes]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(
            "POST",
            f"/v1/tabs/{tab_id}/claude/hook",
            body=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _seed_daemon_tab(database: Path, tmp_path: Path, tab_id: str) -> None:
    now = datetime.now(UTC)
    project_id = "01JBF5SW7QNZ2X5QR8M6K9H001"
    workspace_id = "01JBF5SW7QNZ2X5QR8M6K9H002"
    _ = prepare_database(database, now=now)
    engine = write_engine(sqlite_url(database))
    try:
        with Session(engine) as session:
            session.add(
                Project(
                    id=project_id,
                    name="approval-native-test",
                    root_path=str(tmp_path / "project"),
                    languages=["rust"],
                    created_at=now,
                )
            )
            session.flush()
            session.add(
                Workspace(
                    id=workspace_id,
                    project_id=project_id,
                    mode=WorkspaceMode.MAIN_CHECKOUT,
                    path=str(tmp_path / "project"),
                    branch="main",
                    base_branch="main",
                    base_commit=None,
                    locked=False,
                    dirty_cached=False,
                    created_at=now,
                    removed_at=None,
                )
            )
            session.flush()
            session.add(
                Tab(
                    id=tab_id,
                    project_id=project_id,
                    workspace_id=workspace_id,
                    agent_kind=AgentKind.CLAUDE,
                    account_id=None,
                    title="approval native test",
                    tab_order=0,
                    last_status=TabStatus.IDLE,
                    status_detail=None,
                    plan_node_id=None,
                    context_used_pct=None,
                    session_cost_usd=None,
                    agent_session_id=None,
                    closed_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
    finally:
        engine.dispose()
