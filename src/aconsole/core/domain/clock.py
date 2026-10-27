"""Clock port. The domain does not read the wall clock itself."""

from datetime import UTC, datetime
from typing import override


class Clock:
    """Wall clock. Tests use a subclass that returns a fixed instant."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock(Clock):
    _instant: datetime

    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            message = "clock instant must be timezone-aware"
            raise ValueError(message)
        self._instant = instant

    @override
    def now(self) -> datetime:
        return self._instant
