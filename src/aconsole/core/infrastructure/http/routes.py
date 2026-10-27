"""Ingest routes, body classification, and the bounded event queue."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import final

import msgspec

from aconsole.contracts.ingest import (
    ClaudeHookBase,
    ClaudeNotification,
    ClaudeSessionStart,
    ClaudeStatusline,
    ClaudeStop,
    ClaudeStopFailure,
    CodexHookBase,
    CodexNotify,
    CodexPermissionRequest,
)

MAX_BODY_BYTES = 1024 * 1024
QUEUE_LIMIT = 10_000

_TAILS = {
    "claude/hook": "claude.hook",
    "claude/statusline": "claude.statusline",
    "codex/hook": "codex.hook",
    "codex/notify": "codex.notify",
}
_HOOKS = frozenset({"claude.hook", "codex.hook"})


class _HookName(msgspec.Struct, frozen=True, kw_only=True):
    hook_event_name: str


class _ClaudePermission(ClaudeHookBase, frozen=True, kw_only=True):
    """`permission_suggestions` is present. `Raw | None` cannot decode JSON."""

    tool_name: str
    tool_input: msgspec.Raw
    permission_suggestions: msgspec.Raw


class _ClaudePermissionPlain(ClaudeHookBase, frozen=True, kw_only=True):
    tool_name: str
    tool_input: msgspec.Raw


class _ClaudeTool(ClaudeHookBase, frozen=True, kw_only=True):
    """`tool_input` is present. `Raw | None` cannot decode JSON."""

    tool_name: str
    tool_input: msgspec.Raw


class _ClaudeToolPlain(ClaudeHookBase, frozen=True, kw_only=True):
    tool_name: str


@dataclass(frozen=True, slots=True)
class Route:
    kind: str
    tab_id: str


@dataclass(frozen=True, slots=True)
class IngestItem:
    tab_id: str
    kind: str
    body: bytes


def match(method: str, path: str) -> Route | None:
    """Return the route, or None when the method and path are not ingest."""
    if method == "GET" and path == "/v1/health":
        return Route("health", "")
    if method != "POST" or not path.startswith("/v1/tabs/"):
        return None
    rest = path.removeprefix("/v1/tabs/")
    tab_id, separator, tail = rest.partition("/")
    kind = _TAILS.get(tail)
    if separator == "" or tab_id == "" or kind is None:
        return None
    return Route(kind, tab_id)


def classify(kind: str, body: bytes) -> msgspec.Struct | None:
    """Decode a hook body. Unknown JSON fields are ignored. None means invalid."""
    if kind == "claude.statusline":
        return _decode(body, ClaudeStatusline)
    if kind == "codex.notify":
        return _decode(body, CodexNotify)
    return _classify_hook(kind, body)


def _classify_hook(kind: str, body: bytes) -> msgspec.Struct | None:
    if kind not in _HOOKS:
        return None
    try:
        event = msgspec.json.decode(body, type=_HookName)
    except msgspec.DecodeError:
        return None
    if kind == "claude.hook" and event.hook_event_name == "PermissionRequest":
        return _first(body, _ClaudePermission, _ClaudePermissionPlain)
    if kind == "claude.hook" and event.hook_event_name in {"PreToolUse", "PostToolUse"}:
        return _first(body, _ClaudeTool, _ClaudeToolPlain)
    return _decode(body, _struct_for(kind, event.hook_event_name))


def _first(body: bytes, preferred: type[msgspec.Struct], fallback: type[msgspec.Struct]) -> msgspec.Struct | None:
    decoded = _decode(body, preferred)
    if decoded is not None:
        return decoded
    return _decode(body, fallback)


def _decode(body: bytes, struct: type[msgspec.Struct]) -> msgspec.Struct | None:
    try:
        return msgspec.json.decode(body, type=struct)
    except msgspec.DecodeError:
        return None


def _struct_for(kind: str, event: str) -> type[msgspec.Struct]:
    struct_type: type[msgspec.Struct]
    if kind == "codex.hook":
        struct_type = CodexPermissionRequest if event == "PermissionRequest" else CodexHookBase
        return struct_type
    if event == "Notification":
        struct_type = ClaudeNotification
    elif event == "Stop":
        struct_type = ClaudeStop
    elif event == "StopFailure":
        struct_type = ClaudeStopFailure
    elif event == "SessionStart":
        struct_type = ClaudeSessionStart
    else:
        struct_type = ClaudeHookBase
    return struct_type


@final
class IngestQueue:
    """At most `limit` events. Overflow keeps the latest body per tab."""

    def __init__(self, *, limit: int = QUEUE_LIMIT) -> None:
        if limit < 1:
            message = "ingest queue limit must be positive"
            raise ValueError(message)
        self._limit: int = limit
        self._items: deque[IngestItem] = deque()
        self._coalesced: dict[str, IngestItem] = {}
        self._lost: dict[str, int] = {}
        self._resync: set[str] = set()

    @property
    def depth(self) -> int:
        return len(self._items)

    def push(self, item: IngestItem) -> None:
        if len(self._items) < self._limit:
            self._items.append(item)
            return
        self._lost[item.tab_id] = self._lost.get(item.tab_id, 0) + 1
        self._resync.add(item.tab_id)
        self._coalesced[item.tab_id] = item

    def lost(self, tab_id: str) -> int:
        return self._lost.get(tab_id, 0)

    def needs_resync(self, tab_id: str) -> bool:
        return tab_id in self._resync

    def coalesced(self, tab_id: str) -> IngestItem | None:
        return self._coalesced.get(tab_id)

    def pop(self) -> IngestItem | None:
        """The oldest queued item. When the queue is empty, the latest overflow item of one tab.

        Taking an overflow item clears that tab's loss count and resync flag: the consumer applies the latest
        state, which is all a status needs.
        """
        if self._items:
            return self._items.popleft()
        if not self._coalesced:
            return None
        tab_id = next(iter(self._coalesced))
        item = self._coalesced.pop(tab_id)
        _ = self._lost.pop(tab_id, None)
        self._resync.discard(tab_id)
        return item
