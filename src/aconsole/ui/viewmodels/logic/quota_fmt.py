"""Presentation helpers for account usage limits."""

from collections.abc import Callable

_NAMES = {"opencode": "OpenCode", "grok": "Grok"}
_WARNING_PERCENT = 80
_CRITICAL_PERCENT = 95
_KB = 1024
_MB = 1024 * 1024
# A token is about four bytes of English or JSON. The count is an estimate, and the dialog says so.
_BYTES_PER_TOKEN = 4
_THOUSAND = 1000


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


def quota_level(value: float | None, *, warn: float = _WARNING_PERCENT, critical: float = _CRITICAL_PERCENT) -> str:
    """Classify used quota with the configured warning and critical thresholds."""
    if value is None:
        return "unknown"
    if value >= critical:
        return "critical"
    if value >= warn:
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


def format_bytes(size: int) -> str:
    """Bytes as the dialog shows them: "820 B", "48 KB", "1.4 MB"."""
    if size < _KB:
        return f"{size} B"
    if size < _MB:
        return f"{size / _KB:.0f} KB"
    return f"{size / _MB:.1f} MB"


def approx_tokens(size: int) -> str:
    """About how many tokens ``size`` bytes of text are: "≈900", "≈12k"."""
    tokens = size // _BYTES_PER_TOKEN
    return f"≈{tokens}" if tokens < _THOUSAND else f"≈{tokens / _THOUSAND:.0f}k"


def peak_window[W](windows: tuple[W, ...], used: Callable[[W], float | None]) -> W | None:
    """The window closest to its limit; None when no window has a number."""
    known = [window for window in windows if used(window) is not None]
    return max(known, key=lambda window: used(window) or 0.0) if known else None


def agent_name(kind: str) -> str:
    """How the agent writes its own name."""
    return _NAMES.get(kind, kind.capitalize())
