"""Merge quota observations from several tabs of one account."""

import threading
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class QuotaLevel(StrEnum):
    OK = "ok"
    WARN = "warn"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class QuotaKey(StrEnum):
    FIVE_HOUR = "5h"
    SEVEN_DAY = "7d"
    WEEK = "week"
    MONTH = "month"
    BUDGET = "budget"


_DEFAULT_WARN = 80.0
_DEFAULT_CRITICAL = 95.0


def quota_level(
    *,
    used_pct: float | None,
    resets_at: datetime | None,
    now: datetime,
    warn_pct: float = _DEFAULT_WARN,
    critical_pct: float = _DEFAULT_CRITICAL,
) -> QuotaLevel:
    """APP_API §8: critical, warn, or unknown when there is no fresh observation."""
    if used_pct is None or resets_at is None or resets_at <= now:
        return QuotaLevel.UNKNOWN
    if used_pct >= critical_pct:
        return QuotaLevel.CRITICAL
    if used_pct >= warn_pct:
        return QuotaLevel.WARN
    return QuotaLevel.OK


@dataclass(frozen=True, kw_only=True)
class Observation:
    account_id: str
    key: QuotaKey
    used_pct: float | None
    resets_at: datetime | None
    observed_at: datetime
    tab_id: str

    def level(
        self,
        now: datetime,
        *,
        warn_pct: float = _DEFAULT_WARN,
        critical_pct: float = _DEFAULT_CRITICAL,
    ) -> QuotaLevel:
        return quota_level(
            used_pct=self.used_pct,
            resets_at=self.resets_at,
            now=now,
            warn_pct=warn_pct,
            critical_pct=critical_pct,
        )


def merge(current: Observation | None, incoming: Observation) -> Observation:
    """The later observation wins. Equal timestamps keep the higher used percent."""
    if current is None or incoming.observed_at > current.observed_at:
        return incoming
    if incoming.observed_at < current.observed_at:
        return current
    incoming_used = incoming.used_pct if incoming.used_pct is not None else -1.0
    current_used = current.used_pct if current.used_pct is not None else -1.0
    if incoming_used >= current_used:
        return incoming
    return current


class QuotaBoard:
    """Thread-safe merge of observations for one account seen from many tabs."""

    _lock: threading.Lock
    _items: dict[tuple[str, QuotaKey], Observation]

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items = {}

    def observe(self, observation: Observation) -> Observation:
        key = (observation.account_id, observation.key)
        with self._lock:
            merged = merge(self._items.get(key), observation)
            self._items[key] = merged
            return merged

    def get(self, account_id: str, key: QuotaKey) -> Observation | None:
        with self._lock:
            return self._items.get((account_id, key))
