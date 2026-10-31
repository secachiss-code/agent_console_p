"""Approval hold service, HTTP hook replies, and IPC decision wiring."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import msgspec
import pytest

from aconsole.contracts.app import (
    AppliedResult,
    ApprovalView,
    CommandDetail,
    DecideParams,
    SubscribeParams,
)
from aconsole.contracts.common import (
    AgentKind,
    ApprovalDecision as ContractDecision,
    ApprovalId,
    ApprovalScope,
    ClientRole,
    TabId,
)
from aconsole.contracts.events import ApprovalRequested, ApprovalResolved
from aconsole.contracts.ingest import HookResponse, HookSpecificOutput, PermissionDecision
from aconsole.contracts.ipc import (
    EventEnvelope,
    InitializeParams,
    InitializeResult,
    Notification,
    ProtocolVersion,
    Request,
    ResponseOk,
)
from aconsole.core.application.services.approval_broker import ApprovalHold, HookOutcome
from aconsole.core.domain.approvals import Approval, ApprovalBook, ApprovalPhase, OpenRequest
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.http.app import IngestApp
from aconsole.core.infrastructure.http.auth import TokenBook
from aconsole.core.infrastructure.http.problem import HttpResponse, json_response
from aconsole.core.infrastructure.http.routes import IngestItem, IngestQueue
from aconsole.core.infrastructure.ipc.framing import decode_envelope, encode_line
from aconsole.core.infrastructure.ipc.ring import EventRing
from aconsole.core.infrastructure.ipc.session import Connection

_AT = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
_HOLD = timedelta(seconds=60)
_ENCODER = msgspec.json.Encoder()
_IPC_CAPABILITIES = frozenset({"events.v1", "approvals.inline"})


class _Harness:
    def __init__(self) -> None:
        self.book = ApprovalBook()
        self.events: list[ApprovalRequested | ApprovalResolved] = []
        self.notifications: list[Approval] = []
        self.audit: list[Approval] = []
        self.requested = asyncio.Event()
        self.now_at = _AT
        self.service = ApprovalHold(
            self.book,
            publish=self._publish,
            notify=self.notifications.append,
            audit=self.audit.append,
            now=self._now,
        )

    def _publish(self, event: ApprovalRequested | ApprovalResolved) -> None:
        self.events.append(event)
        if isinstance(event, ApprovalRequested):
            self.requested.set()

    def _now(self) -> datetime:
        return self.now_at


def _request(
    *,
    approval_id: str = "approval-1",
    nonce: str = "nonce-1",
    tab_id: str = "tab-1",
    session_id: str = "session-1",
    tool: str = "Bash",
    command: str = "git status",
    at: datetime = _AT,
    expires_at: datetime = _AT + _HOLD,
    allow_tab_session: bool = True,
) -> OpenRequest:
    return OpenRequest(
        approval_id=approval_id,
        nonce=nonce,
        tab_id=tab_id,
        session_id=session_id,
        tool=tool,
        command=command,
        input_sha256=f"sha256:{session_id}:{command}",
        risk=frozenset(),
        at=at,
        expires_at=expires_at,
        allow_tab_session=allow_tab_session,
    )


def _view(request: OpenRequest, *, agent: AgentKind = AgentKind.CLAUDE) -> ApprovalView:
    return ApprovalView(
        id=ApprovalId(request.approval_id),
        nonce=request.nonce,
        tab_id=TabId(request.tab_id),
        agent=agent,
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
    decision: ContractDecision,
    *,
    scope: ApprovalScope = ApprovalScope.ONCE,
    message: str | None = None,
) -> DecideParams:
    return DecideParams(
        approval_id=ApprovalId(request.approval_id),
        nonce=request.nonce,
        decision=decision,
        scope=scope,
        message=message,
    )


def test_hold_decision_is_idempotent_and_audited_once() -> None:
    asyncio.run(_hold_decision_is_idempotent_and_audited_once())


async def _hold_decision_is_idempotent_and_audited_once() -> None:
    harness = _Harness()
    request = _request()
    waiter = asyncio.create_task(harness.service.hold(request, _view(request)))
    await harness.requested.wait()

    assert not waiter.done()
    assert len(harness.notifications) == 1
    with pytest.raises(DomainError, match="NONCE_MISMATCH"):
        _ = harness.service.decide(
            DecideParams(
                approval_id=ApprovalId(request.approval_id),
                nonce="wrong",
                decision=ContractDecision.ALLOW,
            ),
            at=_AT + timedelta(seconds=1),
        )
    assert not waiter.done()

    applied = harness.service.decide(
        _decide(request, ContractDecision.ALLOW),
        at=_AT + timedelta(seconds=1),
    )
    repeated = harness.service.decide(
        _decide(request, ContractDecision.ALLOW),
        at=_AT + timedelta(seconds=2),
    )
    assert applied == AppliedResult(applied=True)
    assert repeated == AppliedResult(applied=False)
    with pytest.raises(DomainError, match="ALREADY_DECIDED"):
        _ = harness.service.decide(
            _decide(request, ContractDecision.DENY),
            at=_AT + timedelta(seconds=2),
        )

    outcome = await waiter
    assert outcome == HookOutcome(phase=ApprovalPhase.ALLOWED, message=None)
    assert len(harness.events) == 2
    assert isinstance(harness.events[0], ApprovalRequested)
    assert isinstance(harness.events[1], ApprovalResolved)
    assert harness.events[1].outcome == "allowed"
    assert harness.events[1].by == "user"
    assert len(harness.audit) == 1
    assert harness.audit[0].phase is ApprovalPhase.ALLOWED


def test_duplicate_hold_waiter_cancellation_does_not_cancel_shared_future() -> None:
    asyncio.run(_duplicate_hold_waiter_cancellation_does_not_cancel_shared_future())


async def _duplicate_hold_waiter_cancellation_does_not_cancel_shared_future() -> None:
    harness = _Harness()
    request = _request()
    first = asyncio.create_task(harness.service.hold(request, _view(request)))
    await harness.requested.wait()
    duplicate = asyncio.create_task(harness.service.hold(request, _view(request)))
    await asyncio.sleep(0)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await first
    assert not duplicate.done()

    harness.service.expire(at=request.expires_at)
    outcome = await duplicate
    assert outcome == HookOutcome(phase=ApprovalPhase.EXPIRED_TO_NATIVE, message=None)
    assert len(harness.events) == 2
    assert isinstance(harness.events[0], ApprovalRequested)
    assert isinstance(harness.events[1], ApprovalResolved)
    assert harness.events[1].outcome == "expired_to_native"
    assert harness.events[1].by == "timeout"
    assert len(harness.notifications) == 1
    assert len(harness.audit) == 1
    assert harness.audit[0].phase is ApprovalPhase.EXPIRED_TO_NATIVE
    with pytest.raises(DomainError):
        _ = harness.service.decide(_decide(request, ContractDecision.ALLOW), at=request.expires_at)


def test_remembered_deny_rule_returns_deny_outcome_not_native() -> None:
    asyncio.run(_remembered_deny_rule_returns_deny_outcome_not_native())


async def _remembered_deny_rule_returns_deny_outcome_not_native() -> None:
    harness = _Harness()
    seed = _request(command="git status --short")
    waiter = asyncio.create_task(harness.service.hold(seed, _view(seed)))
    await harness.requested.wait()
    _ = harness.service.decide(
        _decide(seed, ContractDecision.DENY, scope=ApprovalScope.TAB_SESSION, message="blocked"),
        at=_AT + timedelta(seconds=1),
    )
    assert (await waiter).phase is ApprovalPhase.DENIED

    repeated = _request(
        approval_id="approval-2",
        nonce="nonce-2",
        session_id="session-2",
        command="git status --short --verbose",
        at=_AT + timedelta(seconds=2),
        expires_at=_AT + timedelta(seconds=2) + _HOLD,
    )
    outcome = await harness.service.hold(repeated, _view(repeated))
    assert outcome.phase is ApprovalPhase.DENIED
    assert outcome.phase is not ApprovalPhase.EXPIRED_TO_NATIVE
    assert len(harness.notifications) == 1
    assert len(harness.audit) == 2


@pytest.mark.parametrize(
    ("kind", "path", "body", "decision", "message"),
    [
        (
            "claude.hook",
            "/v1/tabs/tab-1/claude/hook",
            b'{"session_id":"s-1","hook_event_name":"PermissionRequest","tool_name":"Bash","tool_input":{"command":"git status"}}',
            ContractDecision.ALLOW,
            None,
        ),
        (
            "codex.hook",
            "/v1/tabs/tab-1/codex/hook",
            b'{"session_id":"s-1","hook_event_name":"PermissionRequest","tool_name":"Bash","tool_input":{"command":"git status"}}',
            ContractDecision.DENY,
            "Denied in AConsole",
        ),
    ],
)
def test_permission_hook_route_sends_exactly_one_claude_or_codex_response(
    kind: str,
    path: str,
    body: bytes,
    decision: ContractDecision,
    message: str | None,
) -> None:
    asyncio.run(_permission_hook_route_sends_exactly_one_response(kind, path, body, decision, message))


async def _permission_hook_route_sends_exactly_one_response(
    kind: str,
    path: str,
    body: bytes,
    decision: ContractDecision,
    message: str | None,
) -> None:
    harness = _Harness()
    request = _request(command="git status")
    token_book = TokenBook()
    token = token_book.issue("tab-1")
    handled: list[str] = []

    async def permission_handler(item: IngestItem) -> HttpResponse:
        handled.append(item.kind)
        agent = AgentKind.CLAUDE if item.kind == "claude.hook" else AgentKind.CODEX
        outcome = await harness.service.hold(request, _view(request, agent=agent))
        if outcome.phase is ApprovalPhase.EXPIRED_TO_NATIVE:
            return json_response(b"{}")
        behavior = "allow" if outcome.phase is ApprovalPhase.ALLOWED else "deny"
        payload = HookResponse(
            hook_specific_output=HookSpecificOutput(
                hook_event_name="PermissionRequest",
                decision=PermissionDecision(behavior=behavior, message=outcome.message),
            )
        )
        return json_response(_ENCODER.encode(payload))

    app = IngestApp(token_book, IngestQueue(), permission_handler=permission_handler)
    response = asyncio.create_task(_http_request(app, path, token, body))
    await harness.requested.wait()
    assert not response.done()
    assert handled == [kind]

    _ = harness.service.decide(_decide(request, decision, message=message), at=_AT + timedelta(seconds=1))
    status, response_body, starts, bodies = await response

    assert status == 200
    assert starts == 1
    assert bodies == 1
    hook_response = msgspec.json.decode(response_body, type=HookResponse)
    assert hook_response.hook_specific_output.hook_event_name == "PermissionRequest"
    assert hook_response.hook_specific_output.decision.behavior == decision.value
    assert hook_response.hook_specific_output.decision.message == message


def test_permission_hook_expiry_returns_one_native_response() -> None:
    asyncio.run(_permission_hook_expiry_returns_one_native_response())


async def _permission_hook_expiry_returns_one_native_response() -> None:
    harness = _Harness()
    request = _request()
    token_book = TokenBook()
    token = token_book.issue("tab-1")

    async def permission_handler(_item: IngestItem) -> HttpResponse:
        outcome = await harness.service.hold(request, _view(request))
        if outcome.phase is ApprovalPhase.EXPIRED_TO_NATIVE:
            return json_response(b"{}")
        behavior = "allow" if outcome.phase is ApprovalPhase.ALLOWED else "deny"
        payload = HookResponse(
            hook_specific_output=HookSpecificOutput(
                hook_event_name="PermissionRequest",
                decision=PermissionDecision(behavior=behavior, message=outcome.message),
            )
        )
        return json_response(_ENCODER.encode(payload))

    app = IngestApp(token_book, IngestQueue(), permission_handler=permission_handler)
    body = (
        b'{"session_id":"s-1","hook_event_name":"PermissionRequest",'
        b'"tool_name":"Bash","tool_input":{"command":"git status"}}'
    )
    response = asyncio.create_task(
        _http_request(app, "/v1/tabs/tab-1/claude/hook", token, body)
    )
    await harness.requested.wait()
    harness.service.expire(at=request.expires_at)
    status, response_body, starts, bodies = await response
    assert status == 200
    assert response_body == b"{}"
    assert starts == 1
    assert bodies == 1


async def _http_request(
    app: IngestApp,
    path: str,
    token: str,
    body: bytes,
) -> tuple[int, bytes, int, int]:
    messages: list[Mapping[str, object]] = []
    received = False

    async def receive() -> Mapping[str, object]:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Mapping[str, object]) -> None:
        messages.append(message)

    scope: dict[str, object] = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [
            (b"authorization", f"Bearer {token}".encode()),
            (b"content-type", b"application/json"),
        ],
        "client": ("127.0.0.1", 10000),
        "server": ("127.0.0.1", 8000),
        "scheme": "http",
        "query_string": b"",
    }
    await app(scope, receive, send)

    statuses = [item.get("status") for item in messages if item.get("type") == "http.response.start"]
    response_parts = [item.get("body", b"") for item in messages if item.get("type") == "http.response.body"]
    assert len(statuses) == 1
    assert len(response_parts) == 1
    status = statuses[0]
    body_part = response_parts[0]
    assert isinstance(status, int)
    assert isinstance(body_part, bytes)
    return status, body_part, len(statuses), len(response_parts)


def test_ipc_decide_dispatches_to_approval_hold_and_negotiates_capability() -> None:
    asyncio.run(_ipc_decide_dispatches_to_approval_hold_and_negotiates_capability())


async def _ipc_decide_dispatches_to_approval_hold_and_negotiates_capability() -> None:
    harness = _Harness()
    request = _request()
    waiter = asyncio.create_task(harness.service.hold(request, _view(request)))
    await harness.requested.wait()
    connection = Connection(EventRing(), approval_hold=harness.service)

    initialize = InitializeParams(
        protocol=(ProtocolVersion(major=1, minor=0), ProtocolVersion(major=1, minor=0)),
        role=ClientRole.UI,
        client_name="test-ui",
        client_version="1.0.0",
        pid=os.getpid(),
        capabilities=_IPC_CAPABILITIES,
    )
    initialize_request = Request(
        id=1,
        method="initialize",
        params=msgspec.Raw(_ENCODER.encode(initialize)),
    )
    initialized = connection.receive(encode_line(_ENCODER, initialize_request))
    assert len(initialized) == 1
    envelope = decode_envelope(initialized[0])
    assert isinstance(envelope, ResponseOk)
    negotiated = msgspec.json.decode(envelope.result, type=InitializeResult)
    assert "approvals.inline" in negotiated.capabilities

    decide_request = Request(
        id=2,
        method="approval/decide",
        params=msgspec.Raw(_ENCODER.encode(_decide(request, ContractDecision.ALLOW))),
    )
    frames = connection.receive(encode_line(_ENCODER, decide_request))
    assert len(frames) == 1
    response = decode_envelope(frames[0])
    assert isinstance(response, ResponseOk)
    result = msgspec.json.decode(response.result, type=AppliedResult)
    assert result.applied is True
    outcome = await waiter
    assert outcome.phase is ApprovalPhase.ALLOWED


def test_ipc_approval_events_are_filtered_by_inline_capability() -> None:
    request = _request()
    event = ApprovalRequested(approval=_view(request))

    capable = _initialized_connection(_IPC_CAPABILITIES)
    no_inline = _initialized_connection(frozenset({"events.v1"}))
    assert len(_subscribe(capable, 2)) == 1
    assert len(_subscribe(no_inline, 2)) == 1

    delivered = capable.push_event(event, _AT)
    hidden = no_inline.push_event(event, _AT)
    assert len(delivered) == 1
    assert hidden == ()
    envelope = decode_envelope(delivered[0])
    assert isinstance(envelope, Notification)
    event_envelope = msgspec.json.decode(envelope.params, type=EventEnvelope)
    assert isinstance(event_envelope.event, ApprovalRequested)


def _initialized_connection(capabilities: frozenset[str]) -> Connection:
    connection = Connection(EventRing(), approval_hold=_Harness().service)
    params = InitializeParams(
        protocol=(ProtocolVersion(major=1, minor=0), ProtocolVersion(major=1, minor=0)),
        role=ClientRole.UI,
        client_name="test-ui",
        client_version="1.0.0",
        pid=os.getpid(),
        capabilities=capabilities,
    )
    request = Request(id=1, method="initialize", params=msgspec.Raw(_ENCODER.encode(params)))
    frames = connection.receive(encode_line(_ENCODER, request))
    assert len(frames) == 1
    response = decode_envelope(frames[0])
    assert isinstance(response, ResponseOk)
    return connection


def _subscribe(connection: Connection, request_id: int) -> tuple[bytes, ...]:
    params = SubscribeParams(after_seq=connection.ring.current_seq)
    request = Request(
        id=request_id,
        method="events/subscribe",
        params=msgspec.Raw(_ENCODER.encode(params)),
    )
    return connection.receive(encode_line(_ENCODER, request))
