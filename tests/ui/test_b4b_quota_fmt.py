from __future__ import annotations

import pytest

from aconsole.ui.viewmodels.logic.quota_fmt import (
    format_money,
    format_percent,
    format_tokens,
    format_window_money,
    quota_level,
)


@pytest.mark.parametrize(
    "case",
    [
        (76, False, "76 %"),
        (80, False, "80 %"),
        (94, False, "94 %"),
        (95, False, "95 %"),
        (76, True, "≈76 %"),
        (None, False, "—"),
        (None, True, "—"),
    ],
)
def test_b4b_quota_fmt_examples(
    case: tuple[float | None, bool, str],
) -> None:
    value, estimated, expected = case
    assert format_percent(value, estimated=estimated) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "unknown"),
        (0, "ok"),
        (79.999, "ok"),
        (80, "warn"),
        (94.999, "warn"),
        (95, "critical"),
        (100, "critical"),
    ],
)
def test_b4b_quota_level_thresholds(value: float | None, expected: str) -> None:
    assert quota_level(value) == expected


def test_money_is_spent_over_the_cap_or_left_of_it() -> None:
    assert format_money(None, 20.0) == ""
    assert format_money(6.68, 11.25) == "$6.68 / $11.25"
    assert format_money(391.54, 450.0) == "$391.54 / $450"
    assert format_money(5.2, None) == "$5.20"
    assert format_window_money(6.68, 11.25, "remaining") == "$4.57 / $11.25"
    assert format_window_money(5.2, None, "remaining") == "$5.20"


def test_tokens_read_at_a_glance() -> None:
    assert [format_tokens(count) for count in (950, 12_300, 4_500_000, 1_200_000_000)] == [
        "950", "12.3K", "4.5M", "1.2G",
    ]
