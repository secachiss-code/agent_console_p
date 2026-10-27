"""Hold the latest copy of coalesced events. Facts pass through."""

from aconsole.contracts.events import (
    Banner,
    OperationProgress,
    PlanUpdated,
    QuotaUpdated,
    TabUpdated,
    UiEvent,
)


def coalesce_key(event: UiEvent) -> str | None:
    """Key for events that collapse to the latest value. None means emit now."""
    if isinstance(event, TabUpdated):
        return f"tab:{event.tab.id}"
    if isinstance(event, QuotaUpdated):
        return f"quota:{event.account.id}"
    if isinstance(event, PlanUpdated):
        return f"plan:{event.plan.id}"
    if isinstance(event, Banner):
        return f"banner:{event.id}"
    if isinstance(event, OperationProgress) and not event.done:
        return f"op:{event.operation_id}"
    return None


class Coalescer:
    def __init__(self) -> None:
        self._pending: dict[str, UiEvent] = {}

    def push(self, event: UiEvent) -> UiEvent | None:
        key = coalesce_key(event)
        if key is None:
            return event
        self._pending[key] = event
        return None

    def flush(self) -> tuple[UiEvent, ...]:
        held = tuple(self._pending.values())
        self._pending.clear()
        return held
