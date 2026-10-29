"""Claude rate limits: statusline windows become ``quota_updated`` for one account.

The account comes from ``claude auth status`` through a process port. Credential files
under ``~/.claude/`` are never opened.
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal, Protocol, final

import msgspec

from aconsole.contracts.app import AccountView, QuotaWindowView
from aconsole.contracts.common import AccountId, AgentKind, QuotaLevel
from aconsole.contracts.events import QuotaUpdated
from aconsole.core.application.services.quota_aggregator import QuotaAggregator
from aconsole.core.domain.quota import Observation, QuotaKey, quota_level
from aconsole.core.infrastructure.agents.claude.telemetry import QuotaSignal, read_claude

_AUTH: Final = ("claude", "auth", "status")
_KIND: Final = "claude.statusline"
_IDENTITY: Final = ("email", "accountUuid", "orgId")
_HASH: Final = 12
_PLAN: Final = {"pro": "Pro", "max": "Max", "plus": "Plus", "api": "API"}
_LABEL: Final = 32
_TIMEOUT_S: Final = 5.0


class ProcessPort(Protocol):
    """Run one argv and return its stdout. Callers pass ``claude auth status`` only."""

    def run(self, argv: tuple[str, ...]) -> bytes:
        """Stdout of ``argv``. Empty when the command is not auth status or it fails."""
        ...


@final
class SubprocessPort:
    """``claude auth status`` with an absolute binary. Other argv is refused."""

    _claude: str
    _timeout_s: float

    def __init__(self, claude: str, *, timeout_s: float = _TIMEOUT_S) -> None:
        self._claude = claude
        self._timeout_s = timeout_s

    def run(self, argv: tuple[str, ...]) -> bytes:
        """Stdout of ``claude auth status``. Empty for any other argv or a failed process."""
        if argv != _AUTH or not self._claude.startswith("/"):
            return b""
        try:
            completed = subprocess.run(  # noqa: S603 -- argv is a list; the claude path is absolute
                [self._claude, *argv[1:]],
                check=False,
                capture_output=True,
                timeout=self._timeout_s,
                stdin=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, OSError):
            return b""
        if completed.returncode != 0:
            return b""
        return completed.stdout


@dataclass(frozen=True, slots=True, kw_only=True)
class _Account:
    id: AccountId
    plan_label: str


@final
class ClaudeLimits:
    """Fold a statusline into the shared quota board and one ``quota_updated`` event."""

    _process: ProcessPort
    _aggregator: QuotaAggregator
    _now: Callable[[], datetime]

    def __init__(self, process: ProcessPort, aggregator: QuotaAggregator, now: Callable[[], datetime]) -> None:
        self._process = process
        self._aggregator = aggregator
        self._now = now

    def on_statusline(self, tab_id: str, body: bytes) -> QuotaUpdated | None:
        """Observe 5h and 7d for the account ``claude auth status`` names. None when either is missing."""
        signal = read_claude(body, kind=_KIND)
        if signal.five_hour is None and signal.seven_day is None:
            return None
        account = _account(self._process.run(_AUTH))
        if account is None:
            return None
        now = self._now()
        if signal.five_hour is not None:
            _ = self._aggregator.observe(_observation(account.id, tab_id, QuotaKey.FIVE_HOUR, signal.five_hour, now))
        if signal.seven_day is not None:
            _ = self._aggregator.observe(_observation(account.id, tab_id, QuotaKey.SEVEN_DAY, signal.seven_day, now))
        return QuotaUpdated(
            account=AccountView(
                id=account.id,
                agent=AgentKind.CLAUDE,
                plan_label=account.plan_label,
                quota=_quota(self._aggregator, account.id, now),
            )
        )


def _account(raw: bytes) -> _Account | None:
    try:
        obj = msgspec.json.decode(raw, type=dict[str, msgspec.Raw])
    except msgspec.DecodeError:
        return None
    if not _flag(obj.get("loggedIn")):
        return None
    return _Account(id=_account_id(raw, obj), plan_label=_plan(_text(obj.get("subscriptionType"))))


def _account_id(raw: bytes, obj: dict[str, msgspec.Raw]) -> AccountId:
    token = raw
    for key in _IDENTITY:
        text = _text(obj.get(key))
        if text is not None:
            token = text.encode()
            break
    digest = hashlib.sha256(token).hexdigest()[:_HASH]
    return AccountId(f"claude:{digest}")


def _plan(subscription: str | None) -> str:
    if subscription is None:
        return ""
    named = _PLAN.get(subscription.casefold())
    if named is not None:
        return named
    return subscription[:_LABEL]


def _observation(account_id: str, tab_id: str, key: QuotaKey, window: QuotaSignal, now: datetime) -> Observation:
    return Observation(
        account_id=account_id,
        key=key,
        used_pct=window.used_percentage,
        resets_at=datetime.fromtimestamp(window.resets_at, tz=UTC),
        observed_at=now,
        tab_id=tab_id,
    )


def _quota(aggregator: QuotaAggregator, account_id: str, now: datetime) -> tuple[QuotaWindowView, ...]:
    views: list[QuotaWindowView] = []
    for key in (QuotaKey.FIVE_HOUR, QuotaKey.SEVEN_DAY):
        current = aggregator.current(account_id, key.value)
        if current is not None:
            views.append(_view(current, now))
    return tuple(views)


def _view(observation: Observation, now: datetime) -> QuotaWindowView:
    level = QuotaLevel(
        quota_level(used_pct=observation.used_pct, resets_at=observation.resets_at, now=now).value
    )
    return QuotaWindowView(
        key=_window_key(observation.key),
        used_pct=observation.used_pct,
        resets_at=observation.resets_at,
        level=level,
        observed_at=observation.observed_at,
        estimated=False,
    )


def _window_key(key: QuotaKey) -> Literal["5h", "7d"]:
    if key is QuotaKey.SEVEN_DAY:
        return "7d"
    return "5h"


def _flag(raw: msgspec.Raw | None) -> bool:
    if raw is None:
        return False
    try:
        return msgspec.json.decode(raw, type=bool)
    except msgspec.DecodeError:
        return False


def _text(raw: msgspec.Raw | None) -> str | None:
    if raw is None:
        return None
    try:
        value = msgspec.json.decode(raw, type=str)
    except msgspec.DecodeError:
        return None
    stripped = value.strip()
    if stripped == "":
        return None
    return stripped
