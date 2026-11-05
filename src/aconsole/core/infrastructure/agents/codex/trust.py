"""Trust hashes from ``codex app-server`` ``hooks/list``.

A hook runs only when the hash stored here equals the hash Codex reports now.
This module does not read ``~/.codex`` and does not run Codex.
"""

from __future__ import annotations

from typing import Final, final

import msgspec

_PREFIX: Final = "sha256:"
_HEX: Final = 64


@final
class HookTrustCache:
    """``currentHash`` by hook key. A new observation replaces the old hash."""

    _hooks: dict[str, tuple[str, str]]

    def __init__(self) -> None:
        self._hooks = {}

    def observe(self, key: str, current_hash: str, event_name: str = "") -> None:
        """Remember one hash. Blank keys and hashes that are not ``sha256:`` + 64 hex digits are ignored.

        ``event_name`` is kept only when it is camelCase (``permissionRequest``, ``stop``). A bad name does not
        drop the hash.
        """
        if key == "" or not _hash_ok(current_hash):
            return
        name = event_name if _event_ok(event_name) else ""
        self._hooks[key] = (current_hash, name)

    def hash_for(self, key: str) -> str | None:
        """The hash last observed for ``key``, or None."""
        found = self._hooks.get(key)
        if found is None:
            return None
        return found[0]

    def event_name(self, key: str) -> str | None:
        """The camelCase ``eventName`` last observed for ``key``, or None."""
        found = self._hooks.get(key)
        if found is None or found[1] == "":
            return None
        return found[1]

    def accepts(self, key: str, current_hash: str) -> bool:
        """True only when ``current_hash`` is the one stored for ``key``."""
        stored = self.hash_for(key)
        return stored is not None and stored == current_hash

    def load(self, raw: bytes) -> None:
        """Read a ``hooks/list`` JSON-RPC response, one JSON object per line."""
        for line in raw.splitlines():
            if line.strip() == b"":
                continue
            try:
                obj = msgspec.json.decode(line, type=dict[str, msgspec.Raw])
            except msgspec.DecodeError:
                continue
            result = obj.get("result")
            if result is None:
                continue
            _load_result(self, result)


def _event_ok(value: str) -> bool:
    """CamelCase event names from ``hooks/list``: a lower first letter and no separators."""
    if value == "" or not value[0].islower():
        return False
    return all(char.isascii() and char.isalnum() for char in value)


def _hash_ok(value: str) -> bool:
    if not value.startswith(_PREFIX):
        return False
    digest = value[len(_PREFIX) :]
    if len(digest) != _HEX:
        return False
    return digest.lower() == digest and all(char in "0123456789abcdef" for char in digest)


def _load_result(cache: HookTrustCache, raw: msgspec.Raw) -> None:
    try:
        result = msgspec.json.decode(raw, type=dict[str, msgspec.Raw])
    except msgspec.DecodeError:
        return
    data = result.get("data")
    if data is None:
        return
    try:
        rows = msgspec.json.decode(data, type=list[dict[str, msgspec.Raw]])
    except msgspec.DecodeError:
        return
    for row in rows:
        hooks = row.get("hooks")
        if hooks is None:
            continue
        _load_hooks(cache, hooks)


def _load_hooks(cache: HookTrustCache, raw: msgspec.Raw) -> None:
    try:
        hooks = msgspec.json.decode(raw, type=list[dict[str, msgspec.Raw]])
    except msgspec.DecodeError:
        return
    for hook in hooks:
        key = _text(hook.get("key"))
        current = _text(hook.get("currentHash"))
        event = _text(hook.get("eventName"))
        if key is None or current is None:
            continue
        cache.observe(key, current, "" if event is None else event)


def _text(raw: msgspec.Raw | None) -> str | None:
    if raw is None:
        return None
    try:
        value = msgspec.json.decode(raw, type=str)
    except msgspec.DecodeError:
        return None
    if value == "":
        return None
    return value


class _Hook(msgspec.Struct, rename="camel"):
    key: str
    event_name: str
    handler_type: str
    command: str
    timeout_sec: int
    enabled: bool
    current_hash: str
    trust_status: str
    source: str
    async_: bool = msgspec.field(name="async", default=False)
    matcher: str | None = None
    is_managed: bool = False


class _Group(msgspec.Struct):
    hooks: list[_Hook]
    errors: list[str] = msgspec.field(default_factory=list)


class _Result(msgspec.Struct):
    data: list[_Group]


class _Envelope(msgspec.Struct):
    result: _Result


def verified_hooks(
    raw: bytes, expected: dict[str, tuple[str, str, int]], *, require_trusted: bool = False,
) -> HookTrustCache:
    """Pin only AConsole's exact session definitions, never a user or project hook."""
    envelope = msgspec.json.decode(raw, type=_Envelope)
    cache = HookTrustCache()
    seen: set[str] = set()
    for group in envelope.result.data:
        if group.errors:
            message = "Codex could not load hooks"
            raise ValueError(message)
        for hook in group.hooks:
            wanted = expected.get(hook.key)
            if wanted is None:
                continue
            actual = (hook.event_name, hook.command, hook.timeout_sec)
            if (hook.key in seen or actual != wanted or not hook.enabled or hook.async_ or hook.matcher is not None
                    or hook.handler_type != "command" or hook.source != "sessionFlags" or hook.is_managed
                    or (require_trusted and hook.trust_status != "trusted")):
                message = "Codex hook definition or trust did not match"
                raise ValueError(message)
            seen.add(hook.key)
            cache.observe(hook.key, hook.current_hash, hook.event_name)
    if seen != set(expected) or any(cache.hash_for(key) is None for key in expected):
        message = "Codex did not report every generated hook"
        raise ValueError(message)
    return cache
