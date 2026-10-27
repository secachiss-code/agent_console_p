"""Claude hook and statusline bodies as tab status, quota, and session.

Unknown JSON fields and unknown events are counted and ignored. A body that is not
an object still returns a count; it does not raise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import msgspec

from aconsole.contracts.ingest import RateWindow
from aconsole.core.domain.tab_status import TabStatus

_HOOK: Final = "claude.hook"
_STATUSLINE: Final = "claude.statusline"
_BASE: Final = frozenset({"session_id", "transcript_path", "cwd", "hook_event_name", "permission_mode"})
_FIELDS: Final[dict[str, frozenset[str]]] = {
    "PermissionRequest": _BASE | {"tool_name", "tool_input", "permission_suggestions"},
    "Notification": _BASE | {"message", "notification_type"},
    "Stop": _BASE | {"last_assistant_message", "stop_hook_active"},
    "StopFailure": _BASE | {"error"},
    "SessionStart": _BASE | {"source"},
    "PreToolUse": _BASE | {"tool_name", "tool_input"},
    "PostToolUse": _BASE | {"tool_name", "tool_input"},
}
_STATUS: Final = {
    "SessionStart": TabStatus.IDLE,
    "UserPromptSubmit": TabStatus.WORKING,
    "PreToolUse": TabStatus.WORKING,
    "PostToolUse": TabStatus.WORKING,
    "PermissionRequest": TabStatus.WAITING_APPROVAL,
    "Notification": TabStatus.WAITING_INPUT,
    "Stop": TabStatus.IDLE,
    "SubagentStop": TabStatus.WORKING,
    "StopFailure": TabStatus.ERROR,
    "SessionEnd": TabStatus.IDLE,
}
_QUIET: Final = frozenset({"PreCompact", "PostCompact"})
_LINE_FIELDS: Final = frozenset(
    {"session_id", "transcript_path", "version", "model", "cost", "context_window", "rate_limits"}
)
_MODEL: Final = frozenset({"id", "display_name"})
_COST: Final = frozenset(
    {
        "total_cost_usd",
        "total_duration_ms",
        "total_api_duration_ms",
        "total_lines_added",
        "total_lines_removed",
    }
)
_CONTEXT: Final = frozenset({"context_window_size", "used_percentage", "remaining_percentage"})
_LIMITS: Final = frozenset({"five_hour", "seven_day"})
_WINDOW: Final = frozenset({"used_percentage", "resets_at"})


@dataclass(frozen=True, slots=True, kw_only=True)
class QuotaSignal:
    """One rate-limit window from a Claude statusline."""

    used_percentage: float
    resets_at: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ClaudeTelemetry:
    """Signals from one body. `unknown_events` is 1 when the event is not one we read."""

    status: TabStatus | None
    event: str | None
    session_id: str | None
    five_hour: QuotaSignal | None
    seven_day: QuotaSignal | None
    ignored_fields: int
    unknown_events: int


def read_claude(body: bytes, *, kind: str) -> ClaudeTelemetry:
    """Parse one queued body. Never raises."""
    obj = _object(body)
    if obj is None:
        return _blank(unknown_events=1)
    if kind == _STATUSLINE:
        return _statusline(obj)
    return _hook(obj)


def _blank(*, unknown_events: int, ignored_fields: int = 0, session_id: str | None = None) -> ClaudeTelemetry:
    return ClaudeTelemetry(
        status=None,
        event=None,
        session_id=session_id,
        five_hour=None,
        seven_day=None,
        ignored_fields=ignored_fields,
        unknown_events=unknown_events,
    )


def _object(body: bytes) -> dict[str, msgspec.Raw] | None:
    try:
        return msgspec.json.decode(body, type=dict[str, msgspec.Raw])
    except msgspec.DecodeError:
        return None


def _hook(obj: dict[str, msgspec.Raw]) -> ClaudeTelemetry:
    event = _text(obj.get("hook_event_name"))
    session_id = _text(obj.get("session_id"))
    if event is None or (event not in _STATUS and event not in _QUIET):
        return _blank(unknown_events=1, ignored_fields=len(obj), session_id=session_id)
    known = _FIELDS.get(event, _BASE)
    return ClaudeTelemetry(
        status=_STATUS.get(event),
        event=event,
        session_id=session_id,
        five_hour=None,
        seven_day=None,
        ignored_fields=len(set(obj) - known),
        unknown_events=0,
    )


def _statusline(obj: dict[str, msgspec.Raw]) -> ClaudeTelemetry:
    ignored = len(set(obj) - _LINE_FIELDS)
    ignored += _nested(obj.get("model"), _MODEL)
    ignored += _nested(obj.get("cost"), _COST)
    ignored += _nested(obj.get("context_window"), _CONTEXT)
    five_hour, seven_day, extra = _limits(obj.get("rate_limits"))
    return ClaudeTelemetry(
        status=None,
        event=None,
        session_id=_text(obj.get("session_id")),
        five_hour=five_hour,
        seven_day=seven_day,
        ignored_fields=ignored + extra,
        unknown_events=0,
    )


def _limits(raw: msgspec.Raw | None) -> tuple[QuotaSignal | None, QuotaSignal | None, int]:
    if raw is None:
        return None, None, 0
    obj = _object(bytes(raw))
    if obj is None:
        return None, None, 1
    five_hour, five_ignored = _window(obj.get("five_hour"))
    seven_day, seven_ignored = _window(obj.get("seven_day"))
    return five_hour, seven_day, len(set(obj) - _LIMITS) + five_ignored + seven_ignored


def _window(raw: msgspec.Raw | None) -> tuple[QuotaSignal | None, int]:
    if raw is None:
        return None, 0
    obj = _object(bytes(raw))
    if obj is None:
        return None, 1
    ignored = len(set(obj) - _WINDOW)
    try:
        window = msgspec.json.decode(raw, type=RateWindow)
    except msgspec.DecodeError:
        return None, ignored + 1
    return QuotaSignal(used_percentage=window.used_percentage, resets_at=window.resets_at), ignored


def _nested(raw: msgspec.Raw | None, known: frozenset[str]) -> int:
    if raw is None:
        return 0
    obj = _object(bytes(raw))
    if obj is None:
        return 1
    return len(set(obj) - known)


def _text(raw: msgspec.Raw | None) -> str | None:
    if raw is None:
        return None
    try:
        return msgspec.json.decode(raw, type=str)
    except msgspec.DecodeError:
        return None
