"""Facts the domain emits. They are not the IPC event union."""

from dataclasses import dataclass
from datetime import datetime

from aconsole.core.domain.clock import Clock


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    name: str
    at: datetime

    @classmethod
    def stamp(cls, name: str, clock: Clock) -> DomainEvent:
        return cls(name=name, at=clock.now())
