"""Boundary coverage for remaining quota reset time formatting."""


def test_b4b_time_fmt_examples() -> None:
    from aconsole.ui.viewmodels.logic.time_fmt import format_remaining

    assert format_remaining(102 * 60, "ru") == "1:42"
    assert format_remaining(4 * 24 * 60 * 60, "ru") == "4 дн"
    assert format_remaining(4 * 24 * 60 * 60, "en") == "4d"


def test_b4b_time_fmt_minute_and_day_boundaries() -> None:
    from aconsole.ui.viewmodels.logic.time_fmt import format_remaining

    assert format_remaining(59, "en") == "0:00"
    assert format_remaining(60, "en") == "0:01"
    assert format_remaining(23 * 60 * 60 + 59 * 60 + 59, "ru") == "23:59"
    assert format_remaining(24 * 60 * 60, "ru") == "1 дн"
    assert format_remaining(24 * 60 * 60 - 1, "en") == "23:59"
    assert format_remaining(2 * 24 * 60 * 60 - 1, "en") == "1d"


def test_b4b_time_fmt_expired_is_clamped_to_zero() -> None:
    from aconsole.ui.viewmodels.logic.time_fmt import format_remaining

    assert format_remaining(0, "ru") == "0:00"
    assert format_remaining(-1, "en") == "0:00"
    assert format_remaining(-24 * 60 * 60, "ru") == "0:00"
