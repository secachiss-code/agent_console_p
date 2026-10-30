"""Focused identity checks for incremental tab updates."""

from __future__ import annotations

import msgspec


def test_b2_tab_updated_changes_one_row() -> None:
    from aconsole.contracts.events import TabUpdated
    from aconsole.contracts.ipc import EventEnvelope
    from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
    from aconsole.ui.client.core_client import CoreClient
    from aconsole.ui.viewmodels.tabs_vm import TabsViewModel

    client = CoreClient("unused")
    snapshot = demo_snapshot()
    client.store.apply_snapshot(snapshot, 0)
    vm = TabsViewModel(client)

    ordered_tabs = sorted(snapshot.tabs, key=lambda tab: tab.order)
    rows_before = [vm.store.get(index) for index in range(vm.store.get_n_items())]
    target = ordered_tabs[2]
    renamed = msgspec.structs.replace(target, title="Updated demo tab")

    assert client.store.apply(
        EventEnvelope(
            seq=1,
            at=renamed.status_since,
            event=TabUpdated(tab=renamed, changed=frozenset({"title"})),
        )
    )

    rows_after = [vm.store.get(index) for index in range(vm.store.get_n_items())]
    assert len(rows_after) == len(rows_before)
    assert rows_after == rows_before
    assert [row.get_property("tab-id") for row in rows_after] == [str(tab.id) for tab in ordered_tabs]
    assert [row.get_property("title") for row in rows_after] == [
        "Updated demo tab" if tab.id == target.id else tab.title for tab in ordered_tabs
    ]
