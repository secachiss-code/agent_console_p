"""Typed list store and signal connection on the host GTK."""

import pytest


def test_typed_list_store_round_trip() -> None:
    import gi

    gi.require_version("GObject", "2.0")
    from gi.repository import GObject

    from aconsole.ui.gtk_typed import TypedListStore, connect_typed, gproperty

    class Row(GObject.Object):
        label = gproperty(str, "")

        def __init__(self, label: str) -> None:
            super().__init__()
            self.label = label

    row = Row("one")
    seen: list[str] = []

    def on_notify(item: Row, _pspec: object) -> None:
        seen.append(str(item.label))

    _ = connect_typed(row, "notify::label", on_notify)
    row.label = "two"
    store: TypedListStore[Row] = TypedListStore(Row)
    store.append(row)
    assert store.get(0).label == "two"
    assert "two" in seen
    with pytest.raises(TypeError):
        _ = store.get(1)
