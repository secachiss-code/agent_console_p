"""In-memory event sink. A socket publisher replaces it in the daemon."""

from aconsole.contracts.events import UiEvent


class EventSink:
    def __init__(self) -> None:
        self.events: list[UiEvent] = []

    def emit(self, event: UiEvent) -> None:
        self.events.append(event)
