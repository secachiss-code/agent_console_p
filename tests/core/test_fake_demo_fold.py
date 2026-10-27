"""The demo event burst, folded from an empty snapshot, is the demo snapshot."""

from __future__ import annotations

from aconsole.contracts.ipc import EventEnvelope
from aconsole.core.infrastructure.fake.scenarios import demo_events, demo_snapshot, empty_snapshot
from aconsole.ui.client.store import CoreStore


def test_fake_demo_fold() -> None:
    target = demo_snapshot()
    store = CoreStore()
    store.apply_snapshot(empty_snapshot(), 0)
    assert store.snapshot == empty_snapshot()
    events = demo_events(target)
    at = target.tabs[0].status_since
    for index, event in enumerate(events, start=1):
        applied = store.apply(EventEnvelope(seq=index, at=at, event=event))
        assert applied == (index != len(events))
    assert store.snapshot == target
    assert target != empty_snapshot()
