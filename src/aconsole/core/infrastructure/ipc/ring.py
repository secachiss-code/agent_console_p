"""Global event ring. A seq outside the window means the client must resync."""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime
from typing import final

from aconsole.contracts.events import UiEvent
from aconsole.contracts.ipc import EventEnvelope

RING_SIZE = 4096


@final
class EventRing:
    """The last ``size`` events. Seq starts at 1 and increases by one.

    The daemon appends on the asyncio thread and replays on its request worker, so every access holds a lock.
    """

    def __init__(self, size: int = RING_SIZE) -> None:
        self._items: deque[EventEnvelope] = deque(maxlen=size)
        self._next = 1
        self._lock = threading.Lock()

    def append(self, event: UiEvent, at: datetime) -> EventEnvelope:
        with self._lock:
            item = EventEnvelope(seq=self._next, at=at, event=event)
            self._next += 1
            self._items.append(item)
            return item

    @property
    def current_seq(self) -> int:
        with self._lock:
            if not self._items:
                return 0
            return self._items[-1].seq

    def since(self, after_seq: int) -> tuple[EventEnvelope, ...] | None:
        """Events with seq greater than ``after_seq``, or None when the window was missed."""
        with self._lock:
            return self._since(after_seq)

    def _since(self, after_seq: int) -> tuple[EventEnvelope, ...] | None:
        if not self._items:
            if after_seq == 0:
                return ()
            return None
        oldest = self._items[0].seq
        newest = self._items[-1].seq
        if after_seq > newest or after_seq + 1 < oldest:
            return None
        return tuple(item for item in self._items if item.seq > after_seq)
