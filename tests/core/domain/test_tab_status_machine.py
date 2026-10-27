"""Stateful checks: time moves forward, one notify per episode, attention matches §8."""

from datetime import UTC, datetime, timedelta

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.tab_status import TabStatus, TabStatusTracker, attention_for

_OPENED = datetime(2026, 1, 1, tzinfo=UTC)


class TabMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.tracker = TabStatusTracker(opened_at=_OPENED)
        self.notifies: list[str] = []

    @rule(
        status=st.sampled_from(list(TabStatus)),
        advance_ms=st.integers(min_value=0, max_value=5_000),
        detail=st.none() | st.just("tool"),
    )
    def move(self, status: TabStatus, advance_ms: int, detail: str | None) -> None:
        at = self.tracker.last_at + timedelta(milliseconds=advance_ms)
        try:
            result = self.tracker.apply(status, at, detail)
        except DomainError:
            return
        self._record(notify=result.notify, episode_id=result.episode_id)

    @rule(
        focused=st.booleans(),
        window_active=st.booleans(),
        advance_ms=st.integers(min_value=0, max_value=5_000),
    )
    def focus(self, *, focused: bool, window_active: bool, advance_ms: int) -> None:
        at = self.tracker.last_at + timedelta(milliseconds=advance_ms)
        result = self.tracker.set_focus(focused=focused, window_active=window_active, at=at)
        self._record(notify=result.notify, episode_id=result.episode_id)

    @rule()
    def rewind(self) -> None:
        past = self.tracker.last_at - timedelta(seconds=1)
        status = self.tracker.status
        since = self.tracker.status_since
        episode = self.tracker.episode_id
        try:
            self.tracker.apply(TabStatus.IDLE, past, None)
        except ValueError:
            assert self.tracker.status is status
            assert self.tracker.status_since == since
            assert self.tracker.episode_id == episode
            return
        message = "a backward timestamp was accepted"
        raise AssertionError(message)

    def _record(self, *, notify: bool, episode_id: str | None) -> None:
        if notify and episode_id is not None:
            self.notifies.append(episode_id)

    @invariant()
    def time_is_monotonic(self) -> None:
        assert self.tracker.status_since <= self.tracker.last_at

    @invariant()
    def attention_matches_the_formula(self) -> None:
        expected = attention_for(
            status=self.tracker.status,
            focused=self.tracker.focused,
            window_active=self.tracker.window_active,
        )
        assert self.tracker.attention is expected

    @invariant()
    def at_most_one_notify_per_episode(self) -> None:
        assert len(self.notifies) == len(set(self.notifies))


TestTabMachine = TabMachine.TestCase
TestTabMachine.settings = settings(max_examples=200, stateful_step_count=20, deadline=None)
