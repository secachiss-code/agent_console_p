"""Bound graph replies and bind continuation cursors to one authorized read."""

import hashlib
import hmac
import re
import time
from collections.abc import Callable

from pydantic import BaseModel

CURSOR_PATTERN = r"^[0-9]{1,9}\.[0-9]{1,12}\.[0-9a-f]{32}$"
_PAGE_BYTES = 8 * 1024
_CURSOR_SECONDS = 300


class GraphCursorError(ValueError):
    """A continuation cannot be replayed against this read."""


def graph_digest(*parts: object) -> str:
    """Hash an ordered sequence of already canonical values."""
    return hashlib.sha256(repr(parts).encode()).hexdigest()


def _token(secret: bytes, binding: str, offset: int, expiry: int) -> str:
    prefix = f"{offset}.{expiry}"
    signature = hmac.digest(secret, f"{binding}:{prefix}".encode(), "sha256").hex()[:32]
    return f"{prefix}.{signature}"


def graph_page[Reply: BaseModel](  # noqa: PLR0913 -- paging policy and one authorized projection
    secret: bytes,
    binding: str,
    *,
    cursor: str | None,
    limit: int,
    length: int,
    reply: Callable[[int, int, str | None], Reply],
) -> Reply:
    now = int(time.monotonic())
    expiry = now + _CURSOR_SECONDS
    offset = 0
    if cursor is not None:
        if re.fullmatch(CURSOR_PATTERN, cursor) is None:
            raise GraphCursorError
        offset_text, expiry_text, _signature = cursor.split(".")
        offset, expiry = int(offset_text), int(expiry_text)
        valid = hmac.compare_digest(cursor, _token(secret, binding, offset, expiry))
        if expiry <= now or offset >= length or not valid:
            raise GraphCursorError
    end = min(length, offset + limit)
    while True:
        following = _token(secret, binding, end, expiry) if end < length else None
        result = reply(offset, end, following)
        if len(result.model_dump_json(exclude_none=True, exclude_defaults=True).encode()) <= _PAGE_BYTES:
            return result
        if end <= offset + 1:
            message = "one graph entry exceeds the page budget"
            raise OverflowError(message)
        end -= 1
