"""Drain the ingest queue into the tab book. The HTTP handler only queues; this task applies."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import msgspec

from aconsole.contracts.ingest import CodexNotify
from aconsole.core.infrastructure.http.routes import IngestItem, IngestQueue

_LOG = logging.getLogger(__name__)
_HOOKS = frozenset({"claude.hook", "codex.hook"})


class _HookName(msgspec.Struct, frozen=True, kw_only=True):
    hook_event_name: str


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


async def consume(queue: IngestQueue, ready: asyncio.Event, apply: Callable[[str, str], Awaitable[None]]) -> None:
    """Apply queued items in order until cancelled. `ready` is set whenever the HTTP side queued something."""
    while True:
        _ = await ready.wait()
        ready.clear()
        while (item := queue.pop()) is not None:
            name = hook_event(item)
            if name is None:
                continue
            try:
                await apply(item.tab_id, name)
            except Exception:  # noqa: BLE001 -- one bad item must not stop the consumer
                _LOG.exception("ingest item for %s failed", item.tab_id)
