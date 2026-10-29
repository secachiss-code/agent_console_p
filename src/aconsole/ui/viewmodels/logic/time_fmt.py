"""Formatting of quota reset countdowns."""

_SECONDS_PER_DAY = 24 * 60 * 60


def format_remaining(seconds: int, locale: str) -> str:
    """Return a compact nonnegative duration in the requested language."""
    remaining = max(0, seconds)
    if remaining >= _SECONDS_PER_DAY:
        days = remaining // _SECONDS_PER_DAY
        return f"{days} дн" if locale.startswith("ru") else f"{days}d"
    minutes = remaining // 60
    return f"{minutes // 60}:{minutes % 60:02d}"
