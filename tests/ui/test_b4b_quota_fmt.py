from __future__ import annotations

import pytest

from aconsole.ui.viewmodels.logic.quota_fmt import format_percent, quota_level


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
