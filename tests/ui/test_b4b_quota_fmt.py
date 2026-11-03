from __future__ import annotations

import pytest

from aconsole.ui.viewmodels.logic.quota_fmt import format_money, format_percent, pool_size, quota_level


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


def test_a_pool_known_only_by_its_size_shows_no_invented_spending() -> None:
    assert (format_money(None, 20.0), pool_size(None, 20.0)) == ("", "$20")
    assert (format_money(12.4, 20.0), pool_size(12.4, 20.0)) == ("$12.40 / $20", "")
    assert (format_money(None, None), pool_size(None, None)) == ("", "")


def test_traffic_formats() -> None:
    from aconsole.ui.viewmodels.logic.quota_fmt import approx_tokens, format_bytes

    assert format_bytes(820) == "820 B"
    assert format_bytes(48_200) == "47 KB"
    assert format_bytes(1_500_000) == "1.4 MB"
    assert approx_tokens(3_600) == "≈900"
    assert approx_tokens(48_200) == "≈12k"
