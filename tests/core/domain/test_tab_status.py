"""One test per cell of the tab-status transition table."""

from datetime import UTC, datetime, timedelta

import pytest

from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.tab_status import (
    ALLOWED,
    ATTENTION_STATUSES,
    TabStatus,
    TabStatusTracker,
    attention_for,
)

OPENED = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def _reach(status: TabStatus) -> TabStatusTracker:
    tracker = TabStatusTracker(opened_at=OPENED)
    if status is TabStatus.STARTING:
        return tracker
    tracker.apply(status, OPENED + timedelta(seconds=1), None)
    return tracker


@pytest.mark.parametrize("source", list(TabStatus))
@pytest.mark.parametrize("target", list(TabStatus))
def test_transition_cell(source: TabStatus, target: TabStatus) -> None:
    tracker = _reach(source)
    at = tracker.last_at + timedelta(seconds=1)
    if target in ALLOWED[source]:
        result = tracker.apply(target, at, "detail")
        assert result.status is target
        assert tracker.status is target
        return
    with pytest.raises(DomainError, match="TAB_STATE_CONFLICT"):
        tracker.apply(target, at, "detail")
    assert tracker.status is source


def test_same_status_keeps_since_and_notifies_once() -> None:
    tracker = TabStatusTracker(opened_at=OPENED)
    first = tracker.apply(TabStatus.WAITING_APPROVAL, OPENED + timedelta(seconds=1), "Bash")
    second = tracker.apply(TabStatus.WAITING_APPROVAL, OPENED + timedelta(seconds=2), "Bash: rm")
    assert first.notify is True
    assert first.episode_id == second.episode_id
    assert second.notify is False
    assert second.status_since == first.status_since
    assert tracker.detail == "Bash: rm"


def test_new_episode_after_leaving_the_wait() -> None:
    tracker = TabStatusTracker(opened_at=OPENED)
    first = tracker.apply(TabStatus.ERROR, OPENED + timedelta(seconds=1), "rate_limit")
    tracker.apply(TabStatus.IDLE, OPENED + timedelta(seconds=2), None)
    second = tracker.apply(TabStatus.ERROR, OPENED + timedelta(seconds=3), "overloaded")
    assert first.notify is True
    assert second.notify is True
    assert first.episode_id != second.episode_id


def test_focus_suppresses_then_unfocus_notifies_once() -> None:
    tracker = TabStatusTracker(opened_at=OPENED)
    held = tracker.set_focus(focused=True, window_active=True, at=OPENED)
    entered = tracker.apply(TabStatus.WAITING_INPUT, OPENED + timedelta(seconds=1), None)
    blurred = tracker.set_focus(focused=False, window_active=True, at=OPENED + timedelta(seconds=2))
    again = tracker.set_focus(focused=False, window_active=False, at=OPENED + timedelta(seconds=3))
    assert held.notify is False
    assert entered.notify is False
    assert entered.attention is False
    assert blurred.notify is True
    assert again.notify is False


@pytest.mark.parametrize("status", sorted(ATTENTION_STATUSES, key=str))
def test_attention_formula(status: TabStatus) -> None:
    assert attention_for(status=status, focused=False, window_active=True) is True
    assert attention_for(status=status, focused=True, window_active=False) is True
    assert attention_for(status=status, focused=True, window_active=True) is False


def test_attention_is_false_outside_the_waiting_statuses() -> None:
    quiet = frozenset(TabStatus) - ATTENTION_STATUSES
    for status in quiet:
        assert attention_for(status=status, focused=False, window_active=False) is False


def test_time_must_move_forward_and_be_aware() -> None:
    tracker = TabStatusTracker(opened_at=OPENED)
    with pytest.raises(ValueError, match="monotonic"):
        tracker.apply(TabStatus.IDLE, OPENED - timedelta(seconds=1), None)
    with pytest.raises(ValueError, match="timezone-aware"):
        tracker.apply(TabStatus.IDLE, OPENED.replace(tzinfo=None), None)
    with pytest.raises(ValueError, match="timezone-aware"):
        TabStatusTracker(opened_at=OPENED.replace(tzinfo=None))
    assert tracker.status is TabStatus.STARTING
    assert tracker.status_since == OPENED
