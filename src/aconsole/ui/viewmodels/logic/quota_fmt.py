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
