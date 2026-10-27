"""Coalescer, router, approval broker, and quota aggregator."""

from datetime import UTC, datetime, timedelta

from aconsole.contracts.events import ApprovalRequested, OperationProgress, TabUpdated
from aconsole.core.application.ports.clock import freeze
from aconsole.core.application.ports.events import EventSink
from aconsole.core.application.services.approval_broker import RecordingBroker
from aconsole.core.application.services.coalescer import Coalescer
from aconsole.core.application.services.event_router import EventRouter
from aconsole.core.application.services.quota_aggregator import QuotaAggregator
from aconsole.core.domain.quota import Observation, QuotaKey
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
from aconsole.core.ports.notifier import RecordingNotifier

AT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def test_coalescer_holds_tab_updates_and_releases_facts() -> None:
    snapshot = demo_snapshot()
    tab = snapshot.tabs[0]
    coalescer = Coalescer()
    assert coalescer.push(TabUpdated(tab=tab, changed=frozenset({"status"}))) is None
    assert coalescer.push(TabUpdated(tab=tab, changed=frozenset({"title"}))) is None
    fact = coalescer.push(ApprovalRequested(approval=snapshot.approvals[0]))
    assert fact is not None
    final = OperationProgress(operation_id="op", title="done", fraction=1, done=True, error=None)
    assert coalescer.push(final) is final
    held = coalescer.flush()
    assert len(held) == 1
    assert isinstance(held[0], TabUpdated)


def test_router_stamps_seq_and_fans_out() -> None:
    snapshot = demo_snapshot()
    sink = EventSink()
    router = EventRouter()
    router.add_sink(sink)
    envelopes = router.publish(ApprovalRequested(approval=snapshot.approvals[0]), AT)
    assert envelopes[0].seq == 1
    assert len(sink.events) == 1
    assert freeze(AT).now() == AT


def test_broker_records_the_decision() -> None:
    broker = RecordingBroker()
    RecordingNotifier().notify("title", "body")
    assert broker.decide("a1", behavior="deny") == "deny"
    assert broker.decisions == [("a1", "deny")]


def test_quota_aggregator_keeps_the_later_sample() -> None:
    board = QuotaAggregator()
    early = Observation(
        account_id="claude:demo",
        key=QuotaKey.FIVE_HOUR,
        used_pct=10,
        resets_at=AT,
        observed_at=AT,
        tab_id="t1",
    )
    later = Observation(
        account_id="claude:demo",
        key=QuotaKey.FIVE_HOUR,
        used_pct=40,
        resets_at=AT,
        observed_at=AT + timedelta(seconds=1),
        tab_id="t2",
    )
    board.observe(early)
    assert board.observe(later).used_pct == 40
    assert board.current("claude:demo", "5h") is later
