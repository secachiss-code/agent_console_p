"""Column types for the SQLite store. Aware datetimes and msgspec JSON only."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, final, override

import msgspec
from sqlalchemy import JSON, String
from sqlalchemy.types import TypeDecorator

if TYPE_CHECKING:
    from sqlalchemy.engine import Dialect


@final
class UtcDateTime(TypeDecorator[datetime]):
    """ISO-8601 UTC. Naive values are rejected."""

    impl = String(32)
    cache_ok = True

    @override
    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            message = "datetime must be timezone-aware"
            raise ValueError(message)
        return value.astimezone(UTC).isoformat()

    @override
    def process_result_value(self, value: str | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            message = "stored datetime is naive"
            raise ValueError(message)
        return parsed.astimezone(UTC)


@final
class JsonList(TypeDecorator[list[str]]):
    """A JSON array of strings, checked by msgspec."""

    impl = JSON()
    cache_ok = True

    @override
    def process_bind_param(self, value: list[str] | None, dialect: Dialect) -> list[str] | None:
        if value is None:
            return None
        raw = msgspec.json.encode(value)
        return msgspec.json.decode(raw, type=list[str])

    @override
    def process_result_value(self, value: object, dialect: Dialect) -> list[str] | None:
        if value is None:
            return None
        raw = msgspec.json.encode(value)
        return msgspec.json.decode(raw, type=list[str])


@final
class JsonObject(TypeDecorator[dict[str, object]]):
    """A JSON object, checked by msgspec. Not a bare dict in the repository layer."""

    impl = JSON()
    cache_ok = True

    @override
    def process_bind_param(self, value: dict[str, object] | None, dialect: Dialect) -> dict[str, object] | None:
        return _object(value)

    @override
    def process_result_value(self, value: object, dialect: Dialect) -> dict[str, object] | None:
        return _object(value)


def _object(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    raw = msgspec.json.encode(value)
    return msgspec.json.decode(raw, type=dict[str, object])
