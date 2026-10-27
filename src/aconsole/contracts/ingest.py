"""HTTP ingest bodies from Claude and Codex hooks.

Unknown fields are ignored: these structs do not set forbid_unknown_fields.
Dispatch on hook_event_name is manual; the agents do not send our tag.
"""

from typing import Literal

import msgspec


class StatusModel(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    display_name: str | None = None


class StatusCost(msgspec.Struct, frozen=True, kw_only=True):
    total_cost_usd: float | None = None
    total_duration_ms: int | None = None
    total_api_duration_ms: int | None = None
    total_lines_added: int | None = None
    total_lines_removed: int | None = None


class StatusContext(msgspec.Struct, frozen=True, kw_only=True):
    context_window_size: int | None = None
    used_percentage: float | None = None
    remaining_percentage: float | None = None


class RateWindow(msgspec.Struct, frozen=True, kw_only=True):
    used_percentage: float
    resets_at: int


class StatusRateLimits(msgspec.Struct, frozen=True, kw_only=True):
    five_hour: RateWindow | None = None
    seven_day: RateWindow | None = None


class ClaudeHookBase(msgspec.Struct, frozen=True, kw_only=True):
    session_id: str
    transcript_path: str | None = None
    cwd: str | None = None
    hook_event_name: str
    permission_mode: str | None = None


class ClaudePermissionRequest(ClaudeHookBase, frozen=True, kw_only=True):
    tool_name: str
    tool_input: msgspec.Raw
    permission_suggestions: msgspec.Raw | None = None


class ClaudeNotification(ClaudeHookBase, frozen=True, kw_only=True):
    message: str | None = None
    notification_type: str | None = None


class ClaudeStop(ClaudeHookBase, frozen=True, kw_only=True):
    last_assistant_message: str | None = None
    stop_hook_active: bool = False


class ClaudeStopFailure(ClaudeHookBase, frozen=True, kw_only=True):
    error: str | None = None


class ClaudeSessionStart(ClaudeHookBase, frozen=True, kw_only=True):
    source: str | None = None


class ClaudeToolEvent(ClaudeHookBase, frozen=True, kw_only=True):
    tool_name: str
    tool_input: msgspec.Raw | None = None


class ClaudeStatusline(msgspec.Struct, frozen=True, kw_only=True):
    session_id: str
    transcript_path: str | None = None
    version: str | None = None
    model: StatusModel | None = None
    cost: StatusCost | None = None
    context_window: StatusContext | None = None
    rate_limits: StatusRateLimits | None = None


class CodexHookBase(msgspec.Struct, frozen=True, kw_only=True):
    session_id: str
    turn_id: str | None = None
    transcript_path: str | None = None
    cwd: str | None = None
    hook_event_name: str
    model: str | None = None


class CodexPermissionRequest(CodexHookBase, frozen=True, kw_only=True):
    tool_name: str
    tool_input: msgspec.Raw
    permission_mode: str | None = None


class CodexNotify(msgspec.Struct, frozen=True, kw_only=True):
    type: str
    thread_id: str | None = None
    turn_id: str | None = None
    cwd: str | None = None
    last_assistant_message: str | None = None


class PermissionDecision(msgspec.Struct, frozen=True, kw_only=True):
    behavior: Literal["allow", "deny"]
    message: str | None = None


class HookSpecificOutput(msgspec.Struct, frozen=True, kw_only=True):
    hook_event_name: str = msgspec.field(name="hookEventName")
    decision: PermissionDecision


class HookResponse(msgspec.Struct, frozen=True, kw_only=True):
    hook_specific_output: HookSpecificOutput = msgspec.field(name="hookSpecificOutput")


class EmptyObject(msgspec.Struct, frozen=True, kw_only=True):
    pass


class Health(msgspec.Struct, frozen=True, kw_only=True):
    ok: bool
    version: str


class Problem(msgspec.Struct, frozen=True, kw_only=True):
    type: str
    title: str
    status: int
    detail: str
    instance: str
    reason: str
    retryable: bool
