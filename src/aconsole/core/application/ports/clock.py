"""Clock used by application services. The domain clock is the implementation."""

from datetime import datetime

from aconsole.core.domain.clock import Clock, FrozenClock


def freeze(at: datetime) -> Clock:
    return FrozenClock(at)
