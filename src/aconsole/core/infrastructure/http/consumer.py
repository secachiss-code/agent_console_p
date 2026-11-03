"""Drain the ingest queue into the tab book. The HTTP handler only queues; this task applies."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import msgspec

from aconsole.contracts.ingest import CodexNotify
from aconsole.core.infrastructure.agents.claude.telemetry import read_claude
from aconsole.core.infrastructure.http.routes import IngestItem, IngestQueue

_LOG = logging.getLogger(__name__)
_HOOKS = frozenset({"claude.hook", "codex.hook"})
_IDLE_PROMPT = "idle_prompt"
_CLAUDE = frozenset({"claude.hook", "claude.statusline"})


class _HookName(msgspec.Struct, frozen=True, kw_only=True):
    hook_event_name: str


class _Prompt(msgspec.Struct, frozen=True, kw_only=True):
    hook_event_name: str
    prompt: str = ""


class _NotificationType(msgspec.Struct, frozen=True, kw_only=True):
    notification_type: str | None = None


def hook_event(item: IngestItem) -> str | None:
    """The event name that drives the tab status: a hook's `hook_event_name`, or a Codex notify `type`.

    The statusline carries no status. The body was validated when it was queued; a body that still does not decode
    is skipped.
    """
    try:
        if item.kind in _HOOKS:
            return msgspec.json.decode(item.body, type=_HookName).hook_event_name
        if item.kind == "codex.notify":
            return msgspec.json.decode(item.body, type=CodexNotify).type
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


async def consume(
    queue: IngestQueue,
    ready: asyncio.Event,
    apply: Callable[[str, str], Awaitable[None]],
    on_prompt: Callable[[str, str], Awaitable[None]] | None = None,
) -> None:
    """Apply queued items in order until cancelled. `ready` is set whenever the HTTP side queued something."""
    while True:
        _ = await ready.wait()
        ready.clear()
        while (item := queue.pop()) is not None:
            try:
                name = _event_name(item)
                if name is not None:
                    await apply(item.tab_id, name)
                prompt = submitted_prompt(item)
                if prompt is not None and on_prompt is not None:
                    await on_prompt(item.tab_id, prompt)
            except Exception:  # noqa: BLE001 -- one bad item must not stop the consumer
                _LOG.exception("ingest item for %s failed", item.tab_id)


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
