"""Formatting of quota reset countdowns."""

from aconsole.ui.i18n import _

_SECONDS_PER_DAY = 24 * 60 * 60


def format_remaining(seconds: int, locale: str) -> str:
    """Return a compact nonnegative duration in the requested language."""
    remaining = max(0, seconds)
    if remaining >= _SECONDS_PER_DAY:
        days = remaining // _SECONDS_PER_DAY
        return _("duration.days", days, lang=locale).format(count=days)
    minutes = remaining // 60
    return f"{minutes // 60}:{minutes % 60:02d}"
