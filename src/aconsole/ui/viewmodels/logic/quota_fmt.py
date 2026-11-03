"""Presentation helpers for account usage limits."""

from collections.abc import Callable

_NAMES = {"opencode": "OpenCode", "grok": "Grok"}
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


def format_money(spent: float | None, limit: float | None) -> str:
    """Dollars spent, over the cap when there is one: "$12.40 / $20", or "$5.20" for an uncapped pool."""
    if spent is None:
        return ""
    shown = f"${spent:,.2f}"
    return f"{shown} / ${f'{limit:,.2f}'.removesuffix('.00')}" if limit else shown


def format_window_money(spent: float | None, limit: float | None, display: str = "used") -> str:
    """The money for the settings' choice: what was spent, or what is left of the cap."""
    if display == "remaining" and spent is not None and limit:
        return format_money(max(0.0, limit - spent), limit)
    return format_money(spent, limit)


def format_tokens(count: int) -> str:
    """A token count at a glance: 950, 12.3K, 4.5M, 1.2G."""
    for size, unit in ((1_000_000_000, "G"), (1_000_000, "M"), (1_000, "K")):
        if count >= size:
            return f"{count / size:.1f}{unit}"
    return str(count)


def peak_window[W](windows: tuple[W, ...], used: Callable[[W], float | None]) -> W | None:
    """The window closest to its limit; None when no window has a number."""
    known = [window for window in windows if used(window) is not None]
    return max(known, key=lambda window: used(window) or 0.0) if known else None


def agent_name(kind: str) -> str:
    """How the agent writes its own name."""
    return _NAMES.get(kind, kind.capitalize())
