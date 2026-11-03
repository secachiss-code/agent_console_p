"""What Codex says about money past the plan: credits bought, and a workspace's monthly spend cap."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from aconsole.core.application.services.quota_aggregator import QuotaAggregator
from aconsole.core.infrastructure.agents.codex.limits import CodexLimits

_NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)
_WINDOWS = {
    "planType": "plus",
    "primary": {"usedPercent": 7, "windowDurationMins": 300, "resetsAt": 1_900_000_000},
    "secondary": {"usedPercent": 28, "windowDurationMins": 10080, "resetsAt": 1_900_000_000},
}


def _reply(**extra: object) -> bytes:
    return json.dumps({"id": 2, "result": {"rateLimits": {**_WINDOWS, **extra}}}).encode()


def test_credits_balance_comes_as_a_string() -> None:
    event = CodexLimits(QuotaAggregator(), lambda: _NOW).on_rate_limits(
        "t", _reply(credits={"hasCredits": True, "unlimited": False, "balance": "412.5"}),
    )
    assert event is not None
    assert (event.account.credits_balance, event.account.credits_unlimited) == (412.5, False)


def test_no_credits_and_unlimited_ones() -> None:
    none = CodexLimits(QuotaAggregator(), lambda: _NOW).on_rate_limits(
        "t", _reply(credits={"hasCredits": False, "unlimited": False, "balance": None}),
    )
    assert none is not None
    assert none.account.credits_balance is None
    endless = CodexLimits(QuotaAggregator(), lambda: _NOW).on_rate_limits(
        "t", _reply(credits={"hasCredits": True, "unlimited": True, "balance": None}),
    )
    assert endless is not None
    assert endless.account.credits_unlimited


def test_a_workspace_spend_cap_is_a_budget_window_without_sums() -> None:
    event = CodexLimits(QuotaAggregator(), lambda: _NOW).on_rate_limits(
        "t", _reply(individualLimit={"limit": "100", "used": "35", "remainingPercent": 65, "resetsAt": 1_900_000_000}),
    )
    assert event is not None
    keys = [window.key for window in event.account.quota]
    assert keys == ["5h", "7d", "budget"]
    budget = event.account.quota[-1]
    assert (budget.used_pct, budget.spent_usd, budget.limit_usd) == (35.0, None, None)
    assert budget.resets_at == datetime.fromtimestamp(1_900_000_000, tz=UTC)
