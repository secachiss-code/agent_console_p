"""Translate permission hooks into approval holds and hook responses."""

import logging
from datetime import UTC, datetime, timedelta
from secrets import token_bytes, token_urlsafe

import msgspec

from aconsole.contracts.app import ApprovalDetail, ApprovalView, CommandDetail, EditDetail, GenericDetail
from aconsole.contracts.common import AgentKind, ApprovalId, RiskTag, TabId
from aconsole.contracts.ingest import HookResponse, HookSpecificOutput, PermissionDecision
from aconsole.core.application.services.approval_broker import ApprovalHold
from aconsole.core.domain.approval_policy import ApprovalPolicyChoice, PolicyVerdict, verdict
from aconsole.core.domain.approvals import ApprovalPhase, OpenRequest, digest_input
from aconsole.core.domain.clock import Clock
from aconsole.core.domain.ids import new_ulid
from aconsole.core.domain.risk import RiskTag as DomainRiskTag
from aconsole.core.domain.risk import classify, classify_write_paths
from aconsole.core.infrastructure.http.permission_details import permission_detail
from aconsole.core.infrastructure.http.problem import HttpResponse, empty_json, json_response
from aconsole.core.infrastructure.http.routes import IngestItem

_LOG = logging.getLogger(__name__)
_ASK = ApprovalPolicyChoice()


class _Permission(msgspec.Struct, frozen=True, kw_only=True):
    session_id: str
    tool_name: str
    tool_input: msgspec.Raw
    cwd: str | None = None
    tool_use_id: str = ""
    hook_event_name: str = "PermissionRequest"


def _risk(detail: ApprovalDetail, *, command: str, cwd: str, worktree: str) -> frozenset[DomainRiskTag]:
    risk = classify(command, cwd=cwd, worktree=worktree)
    if isinstance(detail, EditDetail):
        risk |= classify_write_paths(detail.files, cwd=cwd, worktree=worktree)
    if isinstance(detail, GenericDetail):
        risk |= frozenset({DomainRiskTag.PRIVILEGE})
    return risk


def _preflight(
    item: IngestItem, hook: _Permission, choice: ApprovalPolicyChoice, policy: PolicyVerdict, *, inline: bool,
) -> tuple[PolicyVerdict, HttpResponse | None]:
    if hook.hook_event_name != "PreToolUse" or policy is not PolicyVerdict.ASK:
        return policy, None
    if item.kind == "codex.hook":
        if inline:
            return policy, None
        # Codex 0.169 ignores PreToolUse ask and has no untrusted mode.
        if choice.mode.value == "allow":
            return PolicyVerdict.DENY, None
        return policy, json_response(empty_json())
    if choice.mode.value != "allow":
        return policy, json_response(empty_json())
    response = None if inline else json_response(msgspec.json.encode({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "ask",
        "permissionDecisionReason": "AConsole: dangerous calls require approval",
    }}))
    return policy, response


async def handle_permission(  # noqa: PLR0913 -- policy and presentation are independent inputs
    item: IngestItem, service: ApprovalHold, *, hold_seconds: int, worktree: str,
    choice: ApprovalPolicyChoice = _ASK, inline: bool = True,
) -> HttpResponse:
    hook = msgspec.json.decode(item.body, type=_Permission)
    detail = permission_detail(hook.tool_name, hook.tool_input)
    command = detail.command if isinstance(detail, CommandDetail) else ""
    at = datetime.now(UTC)
    expiry = at + timedelta(seconds=hold_seconds)
    identity = new_ulid(Clock(), token_bytes)
    nonce = token_urlsafe(24)
    cwd = hook.cwd or worktree
    risk = _risk(detail, command=command, cwd=cwd, worktree=worktree)
    if item.kind == "codex.hook" and hook.hook_event_name == "PermissionRequest":
        risk |= frozenset({DomainRiskTag.PRIVILEGE})
    event_id = hook.tool_use_id
    if item.kind == "codex.hook" and event_id:
        # Escalation approval is a separate scope from a pre-tool decision.
        event_id = f"{event_id}:{hook.hook_event_name}"
    request = OpenRequest(approval_id=identity, nonce=nonce, tab_id=item.tab_id, session_id=hook.session_id,
        tool=hook.tool_name, command=command, input_sha256=digest_input(bytes(hook.tool_input)), risk=risk,
        at=at, expires_at=expiry, allow_tab_session=bool(command), event_id=event_id,
        fallback_to_native=not (item.kind == "codex.hook" and hook.hook_event_name == "PreToolUse"))
    view = ApprovalView(id=ApprovalId(identity), nonce=nonce, tab_id=TabId(item.tab_id),
        agent=AgentKind.CLAUDE if item.kind == "claude.hook" else AgentKind.CODEX,
        tool=hook.tool_name, summary=command or hook.tool_name,
        detail=detail, cwd=cwd,
        risk=frozenset(RiskTag(tag.value) for tag in risk), requested_at=at, expires_at=expiry,
        allow_tab_session=bool(command))
    policy, immediate = _preflight(item, hook, choice, verdict(choice, risk), inline=inline)
    if immediate is not None:
        return immediate
    if policy is PolicyVerdict.ASK and not inline:
        return json_response(empty_json())
    outcome = await service.hold(request, view, policy=policy)
    if outcome.phase not in {ApprovalPhase.ALLOWED, ApprovalPhase.DENIED}:
        if item.kind == "codex.hook" and hook.hook_event_name == "PreToolUse":
            return json_response(msgspec.json.encode({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": "AConsole approval was not confirmed",
            }}))
        return json_response(empty_json())
    behavior = "allow" if outcome.phase is ApprovalPhase.ALLOWED else "deny"
    message = (outcome.message or "Denied in AConsole") if behavior == "deny" else None
    if policy is not PolicyVerdict.ASK:
        reason = f"AConsole policy: {choice.mode.value}, dangerous={choice.dangerous.value}"
        _LOG.info("approval policy tab=%s tool=%s decision=%s reason=%s", item.tab_id, hook.tool_name, behavior, reason)
        message = reason if behavior == "deny" else None
    if hook.hook_event_name == "PreToolUse":
        return json_response(msgspec.json.encode({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": behavior,
            "permissionDecisionReason": message or "Allowed by AConsole policy or user",
        }}))
    response = HookResponse(hook_specific_output=HookSpecificOutput(hook_event_name="PermissionRequest",
        decision=PermissionDecision(behavior=behavior, message=message)))
    return json_response(msgspec.json.encode(response))
