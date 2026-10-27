"""Assign a global seq and fan an event out to sinks."""

from datetime import datetime

from aconsole.contracts.events import UiEvent
from aconsole.contracts.ipc import EventEnvelope
from aconsole.core.application.ports.events import EventSink
from aconsole.core.application.services.coalescer import Coalescer


class EventRouter:
    _coalescer: Coalescer
    _seq: int
    _sinks: list[EventSink]

    def __init__(self, coalescer: Coalescer | None = None) -> None:
        self._coalescer = Coalescer() if coalescer is None else coalescer
        self._seq = 0
        self._sinks = []

    def add_sink(self, sink: EventSink) -> None:
        self._sinks.append(sink)

    def publish(self, event: UiEvent, at: datetime) -> tuple[EventEnvelope, ...]:
        immediate = self._coalescer.push(event)
        if immediate is None:
            return ()
        return (self._stamp(immediate, at),)

    def flush(self, at: datetime) -> tuple[EventEnvelope, ...]:
        return tuple(self._stamp(event, at) for event in self._coalescer.flush())

    def _stamp(self, event: UiEvent, at: datetime) -> EventEnvelope:
        self._seq += 1
        envelope = EventEnvelope(seq=self._seq, at=at, event=event)
        for sink in self._sinks:
            sink.emit(event)
        return envelope
