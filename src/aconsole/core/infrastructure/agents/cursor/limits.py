"""Cursor's plan usage becomes ``quota_updated`` for the one account its container is signed in to.

Cursor CLI has no command that prints its limits. Its own backend answers them to the access token the CLI keeps in
``~/.config/cursor/auth.json``, the same way the Cursor dashboard asks. The token is read for each question, sent only
to Cursor, and never logged or kept.
"""

from __future__ import annotations

import json
import logging
import math
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from http.client import HTTPResponse
from pathlib import Path
from typing import Final, final

from aconsole.contracts.app import AccountView, QuotaWindowView
from aconsole.contracts.common import AccountId, AgentKind, QuotaLevel
from aconsole.contracts.events import QuotaUpdated
from aconsole.core.domain.quota import quota_level

_LOG = logging.getLogger(__name__)
_API: Final = "https://api2.cursor.sh/aiserver.v1.DashboardService/"
_USAGE: Final = "GetCurrentPeriodUsage"
_PLAN: Final = "GetPlanInfo"
_TIMEOUT_S: Final = 10.0
_READ_BYTES: Final = 262144
_LABEL: Final = 32
_MAX_PCT: Final = 100.0
# One container, one login: the id stays the same so the card that said "unknown" is the one that gets the numbers.
ACCOUNT_ID: Final = AccountId("cursor:local")


def auth_file(agent_home: Path) -> Path:
    """Where Cursor CLI keeps its tokens on Linux."""
    return agent_home / ".config" / "cursor" / "auth.json"


def access_token(agent_home: Path) -> str | None:
    """The CLI's access token. None when Cursor is not signed in or the file is not what Cursor writes."""
    path = auth_file(agent_home)
    try:
        if path.is_symlink():
            return None
        document: object = json.loads(path.read_text(encoding="utf-8"))  # pyright: ignore[reportAny] -- checked just below
    except (OSError, ValueError):
        return None
    token = document.get("accessToken") if isinstance(document, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return token if isinstance(token, str) and token else None


def ask_usage(token: str) -> tuple[bytes, bytes]:
    """The usage and plan replies. A reply that failed is empty; the reason is logged without the token."""
    return _ask(_USAGE, token), _ask(_PLAN, token)


def _ask(method: str, token: str) -> bytes:
    request = urllib.request.Request(  # noqa: S310 -- a fixed https URL
        _API + method,
        data=b"{}",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
        },
    )
    try:
        answer: HTTPResponse = urllib.request.urlopen(request, timeout=_TIMEOUT_S)  # noqa: S310  # pyright: ignore[reportAny] -- a fixed https URL; urlopen is untyped
        with answer:
            return answer.read(_READ_BYTES)
    except urllib.error.HTTPError as exc:
        _LOG.warning("Cursor %s answered %d", method, exc.code)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        _LOG.warning("cannot ask Cursor %s: %s", method, exc)
    return b""


@final
class CursorLimits:
    """Fold one usage reply into the account card. Cursor counts one window: the billing cycle."""

    _now: Callable[[], datetime]

    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now = now

    def on_usage(self, usage: bytes, plan: bytes) -> QuotaUpdated | None:
        """None when the reply holds no plan usage."""
        window = _window(usage)
        if window is None:
            return None
        used_pct, resets_at = window
        now = self._now()
        level = QuotaLevel(quota_level(used_pct=used_pct, resets_at=resets_at, now=now).value)
        return QuotaUpdated(
            account=AccountView(
                id=ACCOUNT_ID,
                agent=AgentKind.CURSOR,
                plan_label=_plan(plan),
                quota=(
                    QuotaWindowView(
                        key="month", used_pct=used_pct, resets_at=resets_at, level=level, observed_at=now,
                        estimated=False,
                    ),
                ),
            ),
        )


def _window(usage: bytes) -> tuple[float, datetime] | None:
    document = _object(usage)
    plan_usage = document.get("planUsage") if document is not None else None
    if document is None or not isinstance(plan_usage, dict):
        return None
    used = _percent(plan_usage.get("totalPercentUsed"))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    resets_at = _millis(document.get("billingCycleEnd"))
    if used is None or resets_at is None:
        return None
    return used, resets_at


def _plan(plan: bytes) -> str:
    document = _object(plan)
    info = document.get("planInfo") if document is not None else None
    name = info.get("planName") if isinstance(info, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return name.strip()[:_LABEL] if isinstance(name, str) else ""


def _object(body: bytes) -> dict[str, object] | None:
    try:
        document: object = json.loads(body)  # pyright: ignore[reportAny] -- checked just below
    except ValueError:
        return None
    return document if isinstance(document, dict) else None  # pyright: ignore[reportUnknownVariableType]


def _percent(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        return None
    return min(float(value), _MAX_PCT)


def _millis(value: object) -> datetime | None:
    """Unix milliseconds, which the API sends as a string."""
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not value.isdigit():
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
