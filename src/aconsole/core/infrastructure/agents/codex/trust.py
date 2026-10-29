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

    _hashes: dict[str, str]

    def __init__(self) -> None:
        self._hashes = {}

    def observe(self, key: str, current_hash: str) -> None:
        """Remember one hash. Blank keys and hashes that are not ``sha256:`` + 64 hex digits are ignored."""
        if key == "" or not _hash_ok(current_hash):
            return
        self._hashes[key] = current_hash

    def hash_for(self, key: str) -> str | None:
        """The hash last observed for ``key``, or None."""
        return self._hashes.get(key)

    def accepts(self, key: str, current_hash: str) -> bool:
        """True only when ``current_hash`` is the one stored for ``key``."""
        stored = self._hashes.get(key)
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
        if key is None or current is None:
            continue
        cache.observe(key, current)


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
