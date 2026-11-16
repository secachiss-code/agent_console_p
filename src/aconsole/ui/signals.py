"""Explicit ownership for callbacks that would otherwise retain detached GTK widgets."""

from collections.abc import Callable

from gi.repository import GObject


class SignalConnections:
    def __init__(self) -> None:
        self._handlers: list[tuple[GObject.Object, int]] = []

    def connect(self, source: GObject.Object, signal: str, callback: Callable[..., object], *data: object) -> None:
        handler = source.connect(signal, callback, *data)
        self._handlers.append((source, handler))

    def close(self) -> None:
        for source, handler in self._handlers:
            if source.handler_is_connected(handler):
                source.disconnect(handler)
        self._handlers.clear()
