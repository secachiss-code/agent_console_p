"""Tab titles projected from core snapshots and tab events."""

from __future__ import annotations

from typing import TYPE_CHECKING

import msgspec

if TYPE_CHECKING:
    from aconsole.contracts.app import TabView


def test_ui_imports_own_worktree() -> None:
    from pathlib import Path

    import aconsole

    assert aconsole.__file__ is not None
    module_path = Path(aconsole.__file__).resolve()
    expected_src = Path(__file__).resolve().parents[2] / "src"
    assert module_path.is_relative_to(expected_src)


def test_tabs_vm_tracks_ordered_tab_events() -> None:
    from aconsole.contracts.events import TabAdded, TabRemoved, TabUpdated
    from aconsole.contracts.ipc import EventEnvelope
    from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
    from aconsole.ui.client.core_client import CoreClient
    from aconsole.ui.viewmodels.tabs_vm import TabsViewModel

    client = CoreClient("unused")
    state = client.store
    source = demo_snapshot()
    snapshot = msgspec.structs.replace(source, tabs=tuple(reversed(source.tabs)))
    state.apply_snapshot(snapshot, 0)
    vm = TabsViewModel(client)

    def assert_projection(tabs: tuple[TabView, ...]) -> None:
        expected = sorted(tabs, key=lambda tab: tab.order)
        assert vm.model.get_n_items() == len(expected)
        assert [vm.store.get(index).get_property("tab-id") for index in range(len(expected))] == [
            str(tab.id) for tab in expected
        ]
        assert [vm.store.get(index).get_property("title") for index in range(len(expected))] == [
            tab.title for tab in expected
        ]

    ordered = sorted(snapshot.tabs, key=lambda tab: tab.order)
    assert len(ordered) == 5
    assert_projection(snapshot.tabs)

    renamed = msgspec.structs.replace(ordered[0], title="Renamed demo tab")
    assert state.apply(
        EventEnvelope(
            seq=1,
            at=renamed.status_since,
            event=TabUpdated(tab=renamed, changed=frozenset({"title"})),
        )
    )
    assert_projection((renamed, *ordered[1:]))

    removed = ordered[2]
    assert state.apply(
        EventEnvelope(seq=2, at=removed.status_since, event=TabRemoved(tab_id=removed.id))
    )
    remaining = tuple(tab for tab in (renamed, *ordered[1:]) if tab.id != removed.id)
    assert_projection(remaining)

    readded = msgspec.structs.replace(
        removed,
        title="Re-added demo tab",
        order=max(tab.order for tab in remaining) + 1,
    )
    assert state.apply(
        EventEnvelope(seq=3, at=readded.status_since, event=TabAdded(tab=readded))
    )
    assert_projection((*remaining, readded))


def test_tabs_vm_starts_empty_without_a_snapshot() -> None:
    from aconsole.ui.client.core_client import CoreClient
    from aconsole.ui.viewmodels.tabs_vm import TabsViewModel

    vm = TabsViewModel(CoreClient("unused"))
    assert vm.model.get_n_items() == 0


def test_tabs_vm_retained_store_tracks_events_and_snapshot_resync() -> None:
    from aconsole.contracts.events import TabRemoved, TabUpdated
    from aconsole.contracts.ipc import EventEnvelope
    from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
    from aconsole.ui.client.core_client import CoreClient
    from aconsole.ui.viewmodels.tabs_vm import TabsViewModel

    client = CoreClient("unused")
    source = demo_snapshot()
    client.store.apply_snapshot(source, 0)
    vm = TabsViewModel(client)
    retained_store = vm.store

    def assert_retained_projection(tabs: tuple[TabView, ...]) -> None:
        expected = sorted(tabs, key=lambda tab: tab.order)
        assert vm.store is retained_store
        assert retained_store.get_n_items() == len(expected)
        assert [retained_store.get(index).get_property("tab-id") for index in range(len(expected))] == [
            str(tab.id) for tab in expected
        ]
        assert [retained_store.get(index).get_property("title") for index in range(len(expected))] == [
            tab.title for tab in expected
        ]

    assert_retained_projection(source.tabs)

    first = min(source.tabs, key=lambda tab: tab.order)
    renamed = msgspec.structs.replace(first, title="Changed by event")
    assert client.store.apply(
        EventEnvelope(
            seq=1,
            at=renamed.status_since,
            event=TabUpdated(tab=renamed, changed=frozenset({"title"})),
        )
    )
    assert_retained_projection((renamed, *(tab for tab in source.tabs if tab.id != first.id)))

    removed = max(source.tabs, key=lambda tab: tab.order)
    assert client.store.apply(
        EventEnvelope(seq=2, at=removed.status_since, event=TabRemoved(tab_id=removed.id))
    )
    after_remove = tuple(
        tab for tab in (renamed, *(tab for tab in source.tabs if tab.id != first.id)) if tab.id != removed.id
    )
    assert_retained_projection(after_remove)

    resynced_tabs = tuple(
        msgspec.structs.replace(tab, title="Restored by snapshot") if tab.id == removed.id else tab
        for tab in source.tabs
    )
    resynced = msgspec.structs.replace(source, tabs=resynced_tabs)
    client.store.apply_snapshot(resynced, 7)
    assert_retained_projection(resynced.tabs)


def test_tabs_vm_refreshes_from_a_resync_snapshot_without_a_later_tab_event() -> None:
    from aconsole.contracts.events import TabRemoved, UiEvent
    from aconsole.contracts.ipc import EventEnvelope
    from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
    from aconsole.ui.client.core_client import CoreClient
    from aconsole.ui.client.store import CoreStore, event_name
    from aconsole.ui.viewmodels.tabs_vm import TabsViewModel

    client = CoreClient("unused")
    state = client.store
    seen: list[tuple[str, ...]] = []
    events: list[str] = []

    def remember(store: CoreStore) -> None:
        snapshot = store.snapshot
        assert snapshot is not None
        assert store is state
        seen.append(tuple(tab.title for tab in snapshot.tabs))

    def remember_event(event: UiEvent, store: CoreStore) -> None:
        assert store is state
        events.append(event_name(event))

    state.subscribe_snapshot(remember)
    state.subscribe(remember_event)
    source = demo_snapshot()
    state.apply_snapshot(source, 0)
    vm = TabsViewModel(client)
    removed = source.tabs[0]
    assert state.apply(EventEnvelope(seq=1, at=removed.status_since, event=TabRemoved(tab_id=removed.id)))
    assert vm.model.get_n_items() == len(source.tabs) - 1

    restored_tabs = tuple(
        msgspec.structs.replace(tab, title="Restored after resync") if tab.id == removed.id else tab
        for tab in source.tabs
    )
    restored = msgspec.structs.replace(source, tabs=restored_tabs)
    events_before = len(events)
    state.apply_snapshot(restored, 4)
    assert len(events) == events_before
    assert seen == [
        tuple(tab.title for tab in source.tabs),
        tuple(tab.title for tab in restored.tabs),
    ]
    expected = sorted(restored.tabs, key=lambda tab: tab.order)
    assert vm.model.get_n_items() == len(expected)
    assert [vm.store.get(index).get_property("tab-id") for index in range(len(expected))] == [
        str(tab.id) for tab in expected
    ]
    assert [vm.store.get(index).get_property("title") for index in range(len(expected))] == [
        tab.title for tab in expected
    ]


def test_tab_added_for_an_existing_id_does_not_increase_the_tab_count() -> None:
    from aconsole.contracts.events import TabAdded
    from aconsole.contracts.ipc import EventEnvelope
    from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
    from aconsole.ui.client.store import CoreStore

    state = CoreStore()
    source = demo_snapshot()
    state.apply_snapshot(source, 0)
    existing = source.tabs[0]
    revised = msgspec.structs.replace(existing, title="Already present")
    assert state.apply(EventEnvelope(seq=1, at=revised.status_since, event=TabAdded(tab=revised)))
    snapshot = state.snapshot
    assert snapshot is not None
    assert len(snapshot.tabs) == len(source.tabs)
    assert [tab.id for tab in snapshot.tabs] == [tab.id for tab in source.tabs]
    assert snapshot.tabs[0].title == "Already present"
