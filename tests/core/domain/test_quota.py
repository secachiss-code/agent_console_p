"""Quota levels and a two-tab race on one account."""

import threading
from datetime import UTC, datetime, timedelta

from aconsole.core.domain.quota import Observation, QuotaBoard, QuotaKey, QuotaLevel, merge, quota_level

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
FUTURE = NOW + timedelta(hours=2)
PAST = NOW - timedelta(minutes=1)


def _obs(used: float | None, at: datetime, tab: str, resets: datetime | None = FUTURE) -> Observation:
    return Observation(
        account_id="claude:abc",
        key=QuotaKey.FIVE_HOUR,
        used_pct=used,
        resets_at=resets,
        observed_at=at,
        tab_id=tab,
    )


def test_levels_follow_the_thresholds() -> None:
    assert quota_level(used_pct=10, resets_at=FUTURE, now=NOW) is QuotaLevel.OK
    assert quota_level(used_pct=80, resets_at=FUTURE, now=NOW) is QuotaLevel.WARN
    assert quota_level(used_pct=95, resets_at=FUTURE, now=NOW) is QuotaLevel.CRITICAL
    assert quota_level(used_pct=None, resets_at=FUTURE, now=NOW) is QuotaLevel.UNKNOWN
    assert quota_level(used_pct=10, resets_at=PAST, now=NOW) is QuotaLevel.UNKNOWN
    assert quota_level(used_pct=10, resets_at=None, now=NOW) is QuotaLevel.UNKNOWN


def test_merge_keeps_the_later_observation_and_the_higher_tie() -> None:
    early = _obs(10, NOW, "tab-a")
    later = _obs(40, NOW + timedelta(seconds=1), "tab-b")
    assert merge(early, later) is later
    assert merge(later, early) is later
    low = _obs(20, NOW, "tab-a")
    high = _obs(70, NOW, "tab-b")
    assert merge(low, high).used_pct == 70
    assert merge(high, low).used_pct == 70


def test_two_tabs_racing_one_account_keep_the_newest() -> None:
    board = QuotaBoard()
    start = threading.Barrier(2)

    def publish(tab: str, used: float) -> None:
        start.wait()
        for step in range(40):
            at = NOW + timedelta(milliseconds=step)
            board.observe(_obs(used + step, at, tab))

    first = threading.Thread(target=publish, args=("tab-a", 0))
    second = threading.Thread(target=publish, args=("tab-b", 100))
    first.start()
    second.start()
    first.join()
    second.join()
    kept = board.get("claude:abc", QuotaKey.FIVE_HOUR)
    assert kept is not None
    assert kept.observed_at == NOW + timedelta(milliseconds=39)
    assert kept.used_pct == 139.0
    assert kept.level(NOW) is QuotaLevel.CRITICAL
