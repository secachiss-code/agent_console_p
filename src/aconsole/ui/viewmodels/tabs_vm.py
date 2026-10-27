"""Tab titles projected from the core store for the pilot sidebar."""

from typing import override

from gi.repository import Gio, GObject

from aconsole.contracts.events import TabAdded, TabRemoved, TabUpdated, UiEvent
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.client.store import CoreStore
from aconsole.ui.gtk_typed import TypedListStore, gproperty


class TabItem(GObject.Object):
    """A sidebar row shares its title with the typed and GTK models."""

    tab_id: object = gproperty(str, "")
    title: object = gproperty(str, "")

    def __init__(self, tab_id: str, title: str) -> None:
        super().__init__()
        self.set_property("tab-id", tab_id)
        self.set_property("title", title)


class TabListStore(TypedListStore[TabItem]):
    """Stable typed view of the same model used by GTK."""

    def __init__(self) -> None:
        super().__init__(TabItem)
        self.model: Gio.ListStore = Gio.ListStore.new(TabItem)

    @override
    def append(self, item: TabItem) -> None:
        self.model.append(item)

    @override
    def get(self, index: int) -> TabItem:
        item = self.model.get_item(index)
        if not isinstance(item, TabItem):
            message = "list store returned an unexpected item"
            raise TypeError(message)
        return item

    def replace(self, items: list[TabItem]) -> None:
        self.model.splice(0, self.model.get_n_items(), items)

    def get_n_items(self) -> int:
        return self.model.get_n_items()


class TabsViewModel:
    """One instance lives for the client lifetime; no private wrapper access."""

    def __init__(self, client: CoreClient) -> None:
        self._store: TabListStore = TabListStore()
        self.model: Gio.ListStore = self._store.model
        self._refresh(client.store)
        client.store.subscribe(self._on_event)
        client.store.subscribe_snapshot(self._refresh)

    @property
    def store(self) -> TabListStore:
        return self._store

    def _refresh(self, state: CoreStore) -> None:
        snapshot = state.snapshot
        items = [] if snapshot is None else [
            TabItem(str(tab.id), tab.title) for tab in sorted(snapshot.tabs, key=lambda tab: tab.order)
        ]
        self._store.replace(items)

    def _on_event(self, event: UiEvent, state: CoreStore) -> None:
        if isinstance(event, (TabAdded, TabRemoved, TabUpdated)) and not state.reconnecting:
            self._refresh(state)
