"""Tab tokens and the Origin allow-list for ingest."""

from __future__ import annotations

import enum
import hmac
import secrets
from urllib.parse import urlsplit

_LOCAL = frozenset({"localhost", "127.0.0.1", "::1"})
_SCHEMES = frozenset({"http", "https"})


class Decision(enum.Enum):
    OK = "ok"
    UNAUTHORIZED = "unauthorized"
    MISMATCH = "mismatch"


def origin_allowed(value: str | None) -> bool:
    """Missing, the literal ``null``, and local http(s) origins are allowed."""
    if value is None or value == "null":
        return True
    parsed = urlsplit(value)
    host = parsed.hostname
    if host is None:
        return False
    return parsed.scheme.casefold() in _SCHEMES and host.casefold() in _LOCAL


def bearer_token(header: str | None) -> str | None:
    if header is None:
        return None
    scheme, separator, value = header.partition(" ")
    if separator != " " or scheme.casefold() != "bearer" or value == "" or " " in value:
        return None
    return value


class TokenBook:
    """One bearer token per live tab. Use it from the asyncio thread."""

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}

    def issue(self, tab_id: str) -> str:
        if tab_id == "" or "/" in tab_id:
            message = "tab id must be a single path segment"
            raise ValueError(message)
        token = secrets.token_urlsafe(32)
        self._tokens[tab_id] = token
        return token

    def register(self, tab_id: str, token: str) -> None:
        """Accept a token the tab book already issued (it is written into the tab's environment)."""
        if tab_id == "" or "/" in tab_id or token == "":
            message = "tab id must be a single path segment and the token must not be empty"
            raise ValueError(message)
        self._tokens[tab_id] = token

    def drop(self, tab_id: str) -> None:
        if tab_id in self._tokens:
            del self._tokens[tab_id]

    def check(self, authorization: str | None, tab_id: str) -> Decision:
        presented = bearer_token(authorization)
        stored = self._tokens.get(tab_id)
        if stored is not None and presented is not None and hmac.compare_digest(presented, stored):
            return Decision.OK
        if presented is not None and self._matches_other(presented, tab_id):
            return Decision.MISMATCH
        return Decision.UNAUTHORIZED

    def _matches_other(self, presented: str, tab_id: str) -> bool:
        found = False
        for owner, stored in self._tokens.items():
            if owner != tab_id and hmac.compare_digest(presented, stored):
                found = True
        return found
