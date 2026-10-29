"""Codex ``account/rateLimits/read`` becomes ``quota_updated`` for one account.

The JSON-RPC body is the only input. ``~/.codex`` credential files are not opened.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal, final

import msgspec

from aconsole.contracts.app import AccountView, QuotaWindowView
from aconsole.contracts.common import AccountId, AgentKind, QuotaLevel
from aconsole.contracts.events import QuotaUpdated
from aconsole.core.application.services.quota_aggregator import QuotaAggregator
from aconsole.core.domain.quota import Observation, QuotaKey, quota_level

_ANON: Final = b"codex"
_HASH: Final = 12
_LABEL: Final = 32
_MINUTES_5H: Final = 300
_MINUTES_7D: Final = 10080
_WINDOWS: Final = {_MINUTES_5H: QuotaKey.FIVE_HOUR, _MINUTES_7D: QuotaKey.SEVEN_DAY}
_PLAN: Final = {"plus": "Plus", "pro": "Pro", "team": "Team", "business": "Business", "free": "Free"}
_IDENTITY: Final = ("email", "accountId", "account_id")


@dataclass(frozen=True, slots=True, kw_only=True)
class _Window:
    key: QuotaKey
    used_pct: float
    resets_at: int


@final
class CodexLimits:
    """Fold one ``account/rateLimits/read`` result into the shared quota board."""

    _aggregator: QuotaAggregator
    _now: Callable[[], datetime]

    def __init__(self, aggregator: QuotaAggregator, now: Callable[[], datetime]) -> None:
        self._aggregator = aggregator
        self._now = now

    def on_rate_limits(self, tab_id: str, body: bytes) -> QuotaUpdated | None:
        """Observe 5h and 7d. None when the body has neither window."""
        windows = _windows(body)
        if not windows:
            return None
        account_id = _account_id(body)
        now = self._now()
        for window in windows:
            _ = self._aggregator.observe(_observation(account_id, tab_id, window, now))
        return QuotaUpdated(
            account=AccountView(
                id=account_id,
                agent=AgentKind.CODEX,
                plan_label=_plan(body),
                quota=_quota(self._aggregator, account_id, now),
            )
        )


def _observation(account_id: str, tab_id: str, window: _Window, now: datetime) -> Observation:
    return Observation(
        account_id=account_id,
        key=window.key,
        used_pct=window.used_pct,
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
    level = QuotaLevel(quota_level(used_pct=observation.used_pct, resets_at=observation.resets_at, now=now).value)
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


def _windows(body: bytes) -> tuple[_Window, ...]:
    limits = _rate_limits(body)
    if limits is None:
        return ()
    found: list[_Window] = []
    for name in ("primary", "secondary"):
        window = _window(limits.get(name))
        if window is not None:
            found.append(window)
    return tuple(found)


def _window(raw: msgspec.Raw | None) -> _Window | None:
    body = None if raw is None else _object(bytes(raw))
    if body is None:
        return None
    minutes = _int(body.get("windowDurationMins"))
    if minutes is None:
        minutes = _int(body.get("window_minutes"))
    key = None if minutes is None else _WINDOWS.get(minutes)
    if key is None:
        return None
    used = _number(body.get("usedPercent"))
    if used is None:
        used = _number(body.get("used_percent"))
    resets = _int(body.get("resetsAt"))
    if resets is None:
        resets = _int(body.get("resets_at"))
    if used is None or resets is None:
        return None
    return _Window(key=key, used_pct=used, resets_at=resets)


def _rate_limits(body: bytes) -> dict[str, msgspec.Raw] | None:
    obj = _object(body)
    if obj is None:
        return None
    result = obj.get("result")
    inner = obj if result is None else _object(bytes(result))
    if inner is None:
        return None
    raw = inner.get("rateLimits")
    if raw is None:
        raw = inner.get("rate_limits")
    if raw is None:
        return None
    return _object(bytes(raw))


def _account_id(body: bytes) -> AccountId:
    token = _identity(body)
    raw = _ANON if token is None else token.encode()
    return AccountId("codex:" + hashlib.sha256(raw).hexdigest()[:_HASH])


def _identity(body: bytes) -> str | None:
    obj = _object(body)
    if obj is None:
        return None
    result = obj.get("result")
    inner = obj if result is None else _object(bytes(result))
    if inner is None:
        return None
    for key in _IDENTITY:
        text = _text(inner.get(key))
        if text is not None:
            return text
    return None


def _plan(body: bytes) -> str:
    limits = _rate_limits(body)
    if limits is None:
        return ""
    name = _text(limits.get("planType")) or _text(limits.get("plan_type"))
    if name is None:
        return ""
    named = _PLAN.get(name.casefold())
    if named is not None:
        return named
    return name[:_LABEL]


def _object(raw: bytes) -> dict[str, msgspec.Raw] | None:
    try:
        return msgspec.json.decode(raw, type=dict[str, msgspec.Raw])
    except msgspec.DecodeError:
        return None


def _text(raw: msgspec.Raw | None) -> str | None:
    if raw is None:
        return None
    try:
        value = msgspec.json.decode(raw, type=str)
    except msgspec.DecodeError:
        return None
    text = value.strip()
    return text or None


def _int(raw: msgspec.Raw | None) -> int | None:
    if raw is None:
        return None
    try:
        return msgspec.json.decode(raw, type=int)
    except msgspec.DecodeError:
        return None


def _number(raw: msgspec.Raw | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(msgspec.json.decode(raw, type=int))
    except msgspec.DecodeError:
        pass
    try:
        return msgspec.json.decode(raw, type=float)
    except msgspec.DecodeError:
        return None
