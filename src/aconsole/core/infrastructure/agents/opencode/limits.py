"""OpenCode Go's usage becomes ``quota_updated`` for the account its container is signed in to.

OpenCode keeps its logins in the `credential` table of ``~/.local/share/opencode/opencode.db``; before version 2 it
kept them in ``auth.json`` beside it. Its Go subscription answers usage for three windows at ``/zen/go/v1/usage``:
rolling 5 hours, a week and a month. A login without Go gets a refusal and no numbers, and the card says why. The key
is read for each question, sent only to OpenCode, and never logged or kept.
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

from sqlalchemy import Column, Integer, MetaData, String, Table, select
from sqlalchemy.exc import SQLAlchemyError

from aconsole.contracts.app import AccountView, QuotaWindowView
from aconsole.contracts.common import AccountId, AgentKind, QuotaLevel
from aconsole.contracts.events import QuotaUpdated
from aconsole.core.domain.quota import quota_level
from aconsole.core.infrastructure.agents.codex.sessions import readonly_engine

_LOG = logging.getLogger(__name__)
_URL: Final = "https://opencode.ai/zen/go/v1/usage"
# The site's firewall turns away Python's default name with error 1010, before OpenCode hears the question.
_AGENT: Final = "aconsole"
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
# A login that pays as it goes: it has a plan, and no windows to count.
NO_GO_PLAN: Final = "Zen"
_NO_GO_ERROR: Final = "EntitlementError"
_CREDENTIAL: Final = Table(
    "credential",
    MetaData(),
    Column("integration_id", String),
    Column("value", String),
    Column("active", Integer),
)
ACCOUNT_ID: Final = AccountId("opencode:local")


def auth_file(agent_home: Path) -> Path:
    return agent_home / ".local" / "share" / "opencode" / "auth.json"


def signed_in(agent_home: Path) -> bool:
    """A login to any provider: only some of them have a key that can ask for usage."""
    return auth_file(agent_home).is_file() or bool(_stored_logins(_database(agent_home)))


def access_key(agent_home: Path) -> str | None:
    """The key OpenCode signed in with. None without a login, or with only a login of another provider."""
    logins = _logins(agent_home)
    for provider in _PROVIDERS:
        entry = logins.get(provider)
        for field in ("key", "access"):
            key = entry.get(field) if isinstance(entry, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            if isinstance(key, str) and key:
                return key
    return None


def _logins(agent_home: Path) -> dict[str, object]:
    """Provider -> its login, from the database, or from the file of an older OpenCode."""
    return _stored_logins(_database(agent_home)) or _file_logins(agent_home)


def _database(agent_home: Path) -> Path:
    return auth_file(agent_home).with_name("opencode.db")


def _stored_logins(database: Path) -> dict[str, object]:
    if database.is_symlink() or not database.is_file():
        return {}
    engine = readonly_engine(database)
    query = select(_CREDENTIAL.c.integration_id, _CREDENTIAL.c.value).where(_CREDENTIAL.c.active == 1)
    try:
        with engine.connect() as connection:
            rows = connection.execute(query).all()
    except SQLAlchemyError:
        # An older database, or one being created. What was wrong may quote the query, never the keys.
        _LOG.debug("cannot read OpenCode's logins", exc_info=True)
        return {}
    finally:
        engine.dispose()
    found: dict[str, object] = {}
    for provider, value in rows:  # pyright: ignore[reportAny] -- declared as strings above
        try:
            found[str(provider)] = json.loads(str(value))  # pyright: ignore[reportAny] -- checked where it is used
        except ValueError:
            continue
    return found


def _file_logins(agent_home: Path) -> dict[str, object]:
    path = auth_file(agent_home)
    try:
        if path.is_symlink():
            return {}
        document: object = json.loads(path.read_text(encoding="utf-8"))  # pyright: ignore[reportAny] -- checked below
    except (OSError, ValueError):
        return {}
    return {str(name): entry for name, entry in document.items()} if isinstance(document, dict) else {}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]


def ask_usage(key: str) -> bytes:
    """The usage reply, or the refusal's body. Empty when OpenCode could not be reached."""
    request = urllib.request.Request(
        _URL,
        method="GET",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json", "User-Agent": _AGENT},
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
        """None when the reply holds no window: an error, an unknown shape. A login without Go is a plan of its own."""
        if _needs_go(body):
            return QuotaUpdated(
                account=AccountView(id=ACCOUNT_ID, agent=AgentKind.OPENCODE, plan_label=NO_GO_PLAN, quota=()),
            )
        now = self._now()
        windows = _windows(body, now)
        if not windows:
            return None
        account = AccountView(id=ACCOUNT_ID, agent=AgentKind.OPENCODE, plan_label="Go", quota=windows)
        return QuotaUpdated(account=account)


def _needs_go(body: bytes) -> bool:
    try:
        document: object = json.loads(body) if body else None
    except ValueError:
        return False
    error = document.get("error") if isinstance(document, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    kind: object = error.get("type") if isinstance(error, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return isinstance(kind, str) and kind == _NO_GO_ERROR


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
