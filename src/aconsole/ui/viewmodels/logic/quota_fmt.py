"""Presentation helpers for account usage limits."""

_WARNING_PERCENT = 80
_CRITICAL_PERCENT = 95


def format_percent(value: float | None, *, estimated: bool = False) -> str:
    """Format a percentage or an unknown value."""
    if value is None:
        return "—"
    prefix = "≈" if estimated else ""
    return f"{prefix}{value:.0f} %"


def shown_percent(used: float | None, display: str) -> float | None:
    """What the user chose to read: the part used, or the part left."""
    if used is None or display != "remaining":
        return used
    return max(0.0, 100.0 - used)


def quota_level(value: float | None) -> str:
    """Classify usage using the design's warning and critical thresholds."""
    if value is None:
        return "unknown"
    if value >= _CRITICAL_PERCENT:
        return "critical"
    if value >= _WARNING_PERCENT:
        return "warn"
    return "ok"
