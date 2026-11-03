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
        # A pool whose size is known but whose spending is not has no sum to show here; its name carries the size.
        return ""
    shown = f"${spent or 0:,.2f}"
    return f"{shown} / ${limit:,.0f}" if limit else shown


def pool_size(spent: float | None, limit: float | None) -> str:
    """The size of a pool reported only as a share, "$20". Empty when the dollars spent are known too."""
    return f"${limit:,.0f}" if spent is None and limit else ""


def peak_window[W](windows: tuple[W, ...], used: Callable[[W], float | None]) -> W | None:
    """The window closest to its limit; None when no window has a number."""
    known = [window for window in windows if used(window) is not None]
    return max(known, key=lambda window: used(window) or 0.0) if known else None


def agent_name(kind: str) -> str:
    """How the agent writes its own name."""
    return _NAMES.get(kind, kind.capitalize())
