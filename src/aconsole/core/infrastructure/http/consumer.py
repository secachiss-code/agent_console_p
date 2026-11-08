"""Drain the ingest queue into the tab book. The HTTP handler only queues; this task applies."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import msgspec

from aconsole.contracts.ingest import CodexNotify
from aconsole.core.infrastructure.agents.claude.telemetry import read_claude
from aconsole.core.infrastructure.agents.opencode.stream import COST_EVENT
from aconsole.core.infrastructure.http.routes import IngestItem, IngestQueue

_LOG = logging.getLogger(__name__)
_HOOKS = frozenset({"claude.hook", "codex.hook"})
_IDLE_PROMPT = "idle_prompt"
_CLAUDE = frozenset({"claude.hook", "claude.statusline"})
OPENCODE_EVENT = "opencode.event"
_NAMES_SESSION = _CLAUDE | {OPENCODE_EVENT}


class _HookName(msgspec.Struct, frozen=True, kw_only=True):
    hook_event_name: str


class _StatusCost(msgspec.Struct, frozen=True):
    total_cost_usd: float | None = None


class _Statusline(msgspec.Struct, frozen=True):
    cost: _StatusCost | None = None


class _StreamCost(msgspec.Struct, frozen=True):
    hook_event_name: str = ""
    cost_usd: float | None = None


class _Prompt(msgspec.Struct, frozen=True, kw_only=True):
    hook_event_name: str
    prompt: str = ""


class _NotificationType(msgspec.Struct, frozen=True, kw_only=True):
    notification_type: str | None = None


def hook_event(item: IngestItem) -> str | None:
    """The event name that drives the tab status: a hook's `hook_event_name`, or a Codex notify `type`.

    OpenCode's stream is read into the same shape. It is not in `_HOOKS`: its "working" carries no prompt.

    The statusline carries no status. The body was validated when it was queued; a body that still does not decode
    is skipped.
    """
    try:
        if item.kind in _HOOKS or item.kind == OPENCODE_EVENT:
            name = msgspec.json.decode(item.body, type=_HookName).hook_event_name
            return None if name == COST_EVENT else name
        if item.kind == "codex.notify":
            return msgspec.json.decode(item.body, type=CodexNotify).type
    except msgspec.DecodeError:
        return None
    return None


def session_cost(item: IngestItem) -> float | None:
    """What the tab's session has cost so far, in dollars at API prices, when the item says it.

    Claude's statusline carries the session's running total; OpenCode's stream, read by `EventReader`, the tab's.
    """
    try:
        if item.kind == "claude.statusline":
            line = msgspec.json.decode(item.body, type=_Statusline)
            return None if line.cost is None else line.cost.total_cost_usd
        if item.kind == OPENCODE_EVENT:
            said = msgspec.json.decode(item.body, type=_StreamCost)
            return said.cost_usd if said.hook_event_name == COST_EVENT else None
    except msgspec.DecodeError:
        return None
    return None


def submitted_prompt(item: IngestItem) -> str | None:
    """The text of a `UserPromptSubmit` hook, or None for any other item."""
    if item.kind not in _HOOKS:
        return None
    try:
        hook = msgspec.json.decode(item.body, type=_Prompt)
    except msgspec.DecodeError:
        return None
    return hook.prompt if hook.hook_event_name == "UserPromptSubmit" else None


@dataclass(frozen=True, slots=True, kw_only=True)
class IngestHandlers:
    """What the consumer hands each item's meaning to. Only `apply` is required."""

    on_prompt: Callable[[str, str], Awaitable[None]] | None = None
    on_session: Callable[[str, str], Awaitable[None]] | None = None
    on_cost: Callable[[str, float], Awaitable[None]] | None = None
    on_loss: Callable[[str, int], Awaitable[None]] | None = None


async def consume(
    queue: IngestQueue,
    ready: asyncio.Event,
    apply: Callable[[str, str], Awaitable[None]],
    handlers: IngestHandlers | None = None,
) -> None:
    """Apply queued items in order until cancelled. `ready` is set whenever the HTTP side queued something."""
    found = handlers or IngestHandlers()
    on_prompt, on_session, on_cost = found.on_prompt, found.on_session, found.on_cost
    while True:
        _ = await ready.wait()
        ready.clear()
        while (item := queue.pop()) is not None:
            failed = False
            try:
                name = _event_name(item)
                if name is not None:
                    await apply(item.tab_id, name)
                session = _session_id(item)
                if session is not None and on_session is not None:
                    await on_session(item.tab_id, session)
                cost = session_cost(item)
                if cost is not None and on_cost is not None:
                    await on_cost(item.tab_id, cost)
                prompt = submitted_prompt(item)
                if prompt is not None and on_prompt is not None:
                    await on_prompt(item.tab_id, prompt)
            except Exception:  # noqa: BLE001 -- one bad item must not stop the consumer
                _LOG.exception("ingest item for %s failed", item.tab_id)
                failed = True
            lost = queue.take_applied_loss(item.tab_id) + int(failed)
            if lost and found.on_loss is not None:
                try:
                    await found.on_loss(item.tab_id, lost)
                except Exception:  # noqa: BLE001 -- a failed notification must not stop ingest
                    _LOG.exception("ingest loss notification for %s failed", item.tab_id)


class _HasSession(msgspec.Struct, frozen=True):
    session_id: str | None = None


def _session_id(item: IngestItem) -> str | None:
    if item.kind not in _NAMES_SESSION:
        return None
    try:
        return msgspec.json.decode(item.body, type=_HasSession).session_id
    except msgspec.DecodeError:
        return None


def _event_name(item: IngestItem) -> str | None:
    """Claude bodies go through telemetry. Codex still uses the event name alone."""
    if item.kind in _CLAUDE:
        signal = read_claude(item.body, kind=item.kind)
        if signal.status is None or _only_idle(signal.event, item.body):
            return None
        return signal.event
    return hook_event(item)


def _only_idle(event: str | None, body: bytes) -> bool:
    """Claude's "still waiting for your prompt" reminder: nothing is asked, the tab just stays idle."""
    if event != "Notification":
        return False
    try:
        return msgspec.json.decode(body, type=_NotificationType).notification_type == _IDLE_PROMPT
    except msgspec.DecodeError:
        return False
