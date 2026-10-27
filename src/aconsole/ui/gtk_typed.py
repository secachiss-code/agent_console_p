"""Small typed wrappers around PyGObject. Importers pull in GTK."""

from collections.abc import Callable

import gi

gi.require_version("GObject", "2.0")
from gi.repository import Gio, GObject  # noqa: E402


def gproperty(kind: type[object], default: object) -> object:
    return GObject.Property(type=kind, default=default)


def connect_typed(widget: GObject.Object, signal: str, callback: Callable[..., object]) -> int:
    return int(widget.connect(signal, callback))


class TypedListStore[T]:
    """``Gio.ListStore`` that returns the item type it was created with."""

    def __init__(self, item_type: type[T]) -> None:
        self._item_type: type[T] = item_type
        self._store: Gio.ListStore = Gio.ListStore.new(item_type)

    def append(self, item: T) -> None:
        self._store.append(item)

    def get(self, index: int) -> T:
        found = self._store.get_item(index)
        if not isinstance(found, self._item_type):
            message = "list store returned an unexpected item"
            raise TypeError(message)
        return found
