"""Translate permission hooks into approval holds and hook responses."""

from datetime import UTC, datetime, timedelta
from secrets import token_bytes, token_urlsafe

import msgspec

from aconsole.contracts.app import ApprovalView, CommandDetail
from aconsole.contracts.common import AgentKind, ApprovalId, RiskTag, TabId
from aconsole.contracts.ingest import HookResponse, HookSpecificOutput, PermissionDecision
from aconsole.core.application.services.approval_broker import ApprovalHold
from aconsole.core.domain.approvals import ApprovalPhase, OpenRequest, digest_input
from aconsole.core.domain.clock import Clock
from aconsole.core.domain.ids import new_ulid
from aconsole.core.domain.risk import classify
from aconsole.core.infrastructure.http.problem import HttpResponse, empty_json, json_response
from aconsole.core.infrastructure.http.routes import IngestItem


class _Permission(msgspec.Struct, frozen=True, kw_only=True):
    session_id: str
    tool_name: str
    tool_input: msgspec.Raw
    cwd: str | None = None


class _Command(msgspec.Struct, frozen=True, kw_only=True):
    command: str = ""


async def handle_permission(
    item: IngestItem, service: ApprovalHold, *, hold_seconds: int, worktree: str,
) -> HttpResponse:
    hook = msgspec.json.decode(item.body, type=_Permission)
    try:
        command = msgspec.json.decode(hook.tool_input, type=_Command).command
    except msgspec.DecodeError:
        command = ""
    at = datetime.now(UTC)
    expiry = at + timedelta(seconds=hold_seconds)
    identity = new_ulid(Clock(), token_bytes)
    nonce = token_urlsafe(24)
    cwd = hook.cwd or ""
    risk = classify(command, cwd=cwd, worktree=worktree)
    request = OpenRequest(approval_id=identity, nonce=nonce, tab_id=item.tab_id, session_id=hook.session_id,
        tool=hook.tool_name, command=command, input_sha256=digest_input(bytes(hook.tool_input)), risk=risk,
        at=at, expires_at=expiry, allow_tab_session=bool(command))
    view = ApprovalView(id=ApprovalId(identity), nonce=nonce, tab_id=TabId(item.tab_id),
        agent=AgentKind.CLAUDE if item.kind == "claude.hook" else AgentKind.CODEX,
        tool=hook.tool_name, summary=command or hook.tool_name,
        detail=CommandDetail(command=command, args_preview=None), cwd=cwd,
        risk=frozenset(RiskTag(tag.value) for tag in risk), requested_at=at, expires_at=expiry,
        allow_tab_session=bool(command))
    outcome = await service.hold(request, view)
    if outcome.phase not in {ApprovalPhase.ALLOWED, ApprovalPhase.DENIED}:
        return json_response(empty_json())
    behavior = "allow" if outcome.phase is ApprovalPhase.ALLOWED else "deny"
    message = (outcome.message or "Отклонено в AConsole") if behavior == "deny" else None
    response = HookResponse(hook_specific_output=HookSpecificOutput(hook_event_name="PermissionRequest",
        decision=PermissionDecision(behavior=behavior, message=message)))
    return json_response(msgspec.json.encode(response))
