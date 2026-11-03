"""OpenCode Go's usage becomes ``quota_updated`` for the account its container is signed in to.

OpenCode keeps the key it signed in with in ``~/.local/share/opencode/auth.json``. Its Go subscription answers usage
for three windows at ``/zen/go/v1/usage``: rolling 5 hours, a week and a month. A key without Go gets a refusal and no
numbers. The key is read for each question, sent only to OpenCode, and never logged or kept.
"""

from __future__ import annotations

import json
import logging
import math
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http.client import HTTPResponse
from pathlib import Path
from typing import Final, Literal, final

from aconsole.contracts.app import AccountView, QuotaWindowView
from aconsole.contracts.common import AccountId, AgentKind, QuotaLevel
from aconsole.contracts.events import QuotaUpdated
from aconsole.core.domain.quota import quota_level

_LOG = logging.getLogger(__name__)
_URL: Final = "https://opencode.ai/zen/go/v1/usage"
_TIMEOUT_S: Final = 10.0
_READ_BYTES: Final = 65536
_MAX_PCT: Final = 100.0
_MS_THRESHOLD: Final = 10_000_000_000
# The Go key first; a Zen key may carry the subscription too.
_PROVIDERS: Final = ("opencode-go", "opencode")
_WINDOWS: Final[tuple[tuple[Literal["5h", "week", "month"], tuple[str, ...]], ...]] = (
    ("5h", ("rolling", "rollingUsage")),
    ("week", ("weekly", "weeklyUsage")),
    ("month", ("monthly", "monthlyUsage")),
)
ACCOUNT_ID: Final = AccountId("opencode:local")


def auth_file(agent_home: Path) -> Path:
    return agent_home / ".local" / "share" / "opencode" / "auth.json"


def access_key(agent_home: Path) -> str | None:
    """The API key OpenCode signed in with. None without a login, or with only an OAuth login of another provider."""
    path = auth_file(agent_home)
    try:
        if path.is_symlink():
            return None
        document: object = json.loads(path.read_text(encoding="utf-8"))  # pyright: ignore[reportAny] -- checked below
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    for provider in _PROVIDERS:
        entry = document.get(provider)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        key = entry.get("key") if isinstance(entry, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if isinstance(key, str) and key:
            return key
    return None


def ask_usage(key: str) -> bytes:
    """The usage reply, or the refusal's body. Empty when OpenCode could not be reached."""
    request = urllib.request.Request(
        _URL, method="GET", headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    try:
        answer: HTTPResponse = urllib.request.urlopen(request, timeout=_TIMEOUT_S)  # noqa: S310  # pyright: ignore[reportAny] -- a fixed https URL; urlopen is untyped
        with answer:
            return answer.read(_READ_BYTES)
    except urllib.error.HTTPError as exc:
        # A refusal (no Go subscription, a key gone stale) says why in its body, which holds no key.
        body = exc.read(_READ_BYTES)
        return body or f'{{"status": {exc.code}}}'.encode()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        _LOG.warning("cannot ask OpenCode for usage: %s", exc)
    return b""


@final
class OpenCodeLimits:
    """Fold one usage reply into the account card."""

    _now: Callable[[], datetime]

    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now = now

    def on_usage(self, body: bytes) -> QuotaUpdated | None:
        """None when the reply holds no window: a refusal, an error, a shape nobody knows."""
        now = self._now()
        windows = _windows(body, now)
        if not windows:
            return None
        account = AccountView(id=ACCOUNT_ID, agent=AgentKind.OPENCODE, plan_label="Go", quota=windows)
        return QuotaUpdated(account=account)


def _windows(body: bytes, now: datetime) -> tuple[QuotaWindowView, ...]:
    try:
        document: object = json.loads(body) if body else None
    except ValueError:
        return ()
    if not isinstance(document, dict):
        return ()
    usage = document.get("usage", document)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(usage, dict):
        return ()
    found: list[QuotaWindowView] = []
    for key, names in _WINDOWS:
        raw = next((usage.get(name) for name in names if isinstance(usage.get(name), dict)), None)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportUnknownVariableType]
        window = _window(key, raw, now) if isinstance(raw, dict) else None  # pyright: ignore[reportUnknownArgumentType]
        if window is not None:
            found.append(window)
    return tuple(found)


def _window(key: Literal["5h", "week", "month"], raw: dict[object, object], now: datetime) -> QuotaWindowView | None:
    used = _percent(raw.get("percent", raw.get("usagePercent")))
    if raw.get("status") == "rate-limited":
        used = _MAX_PCT
    resets_at = _reset(raw, now)
    if used is None:
        return None
    level = QuotaLevel(quota_level(used_pct=used, resets_at=resets_at, now=now).value)
    return QuotaWindowView(
        key=key, used_pct=used, resets_at=resets_at, level=level, observed_at=now, estimated=False,
    )


def _percent(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        return None
    return min(float(value), _MAX_PCT)


def _reset(raw: dict[object, object], now: datetime) -> datetime | None:
    """`resetsAt` as an ISO time or a Unix time (seconds or milliseconds), else `resetInSec` from now."""
    value = raw.get("resetsAt")
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
        seconds = value / 1000 if value > _MS_THRESHOLD else value
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    after = raw.get("resetInSec")
    if isinstance(after, int | float) and not isinstance(after, bool) and math.isfinite(after) and after >= 0:
        return now + timedelta(seconds=float(after))
    return None
