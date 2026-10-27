"""Tab status tracker. Strings match APP_API TabStatus. Attention is APP_API §8."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from aconsole.core.domain.errors import DomainError

_CONFLICT_CODE = 2004
_CONFLICT_NAME = "TAB_STATE_CONFLICT"


class TabStatus(StrEnum):
    STARTING = "starting"
    IDLE = "idle"
    WORKING = "working"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_INPUT = "waiting_input"
    ERROR = "error"
    EXITED = "exited"
    UNKNOWN = "unknown"


ATTENTION_STATUSES = frozenset(
    {
        TabStatus.WAITING_APPROVAL,
        TabStatus.WAITING_INPUT,
        TabStatus.ERROR,
    }
)

_LIVE = frozenset(
    {
        TabStatus.IDLE,
        TabStatus.WORKING,
        TabStatus.WAITING_APPROVAL,
        TabStatus.WAITING_INPUT,
        TabStatus.ERROR,
        TabStatus.EXITED,
        TabStatus.UNKNOWN,
    }
)

# Every status has an explicit set of legal targets, including staying put.
ALLOWED: dict[TabStatus, frozenset[TabStatus]] = {
    TabStatus.STARTING: frozenset(TabStatus),
    TabStatus.IDLE: _LIVE,
    TabStatus.WORKING: _LIVE,
    TabStatus.WAITING_APPROVAL: _LIVE,
    TabStatus.WAITING_INPUT: _LIVE,
    TabStatus.ERROR: frozenset(
        {
            TabStatus.ERROR,
            TabStatus.IDLE,
            TabStatus.WORKING,
            TabStatus.STARTING,
            TabStatus.EXITED,
            TabStatus.UNKNOWN,
        }
    ),
    TabStatus.EXITED: frozenset({TabStatus.EXITED, TabStatus.STARTING}),
    TabStatus.UNKNOWN: frozenset(TabStatus),
}


def attention_for(*, status: TabStatus, focused: bool, window_active: bool) -> bool:
    """True when the tab needs the user and is not the focused tab of an active window."""
    waiting = status in ATTENTION_STATUSES
    return waiting and not (focused and window_active)


@dataclass(frozen=True, kw_only=True)
class TrackResult:
    status: TabStatus
    status_since: datetime
    detail: str | None
    attention: bool
    notify: bool
    episode_id: str | None


class TabStatusTracker:
    """One tab. Time of accepted events is monotonic. One notify per waiting episode."""

    _status: TabStatus
    _since: datetime
    _last_at: datetime
    _detail: str | None
    _focused: bool
    _window_active: bool
    _episode_id: str | None
    _episode_seq: int
    _notified: set[str]

    def __init__(self, *, opened_at: datetime) -> None:
        if opened_at.tzinfo is None:
            message = "opened_at must be timezone-aware"
            raise ValueError(message)
        self._status = TabStatus.STARTING
        self._since = opened_at
        self._last_at = opened_at
        self._detail = None
        self._focused = False
        self._window_active = False
        self._episode_id = None
        self._episode_seq = 0
        self._notified = set()

    @property
    def status(self) -> TabStatus:
        return self._status

    @property
    def status_since(self) -> datetime:
        return self._since

    @property
    def last_at(self) -> datetime:
        return self._last_at

    @property
    def detail(self) -> str | None:
        return self._detail

    @property
    def focused(self) -> bool:
        return self._focused

    @property
    def window_active(self) -> bool:
        return self._window_active

    @property
    def episode_id(self) -> str | None:
        return self._episode_id

    @property
    def attention(self) -> bool:
        return attention_for(status=self._status, focused=self._focused, window_active=self._window_active)

    def apply(self, status: TabStatus, at: datetime, detail: str | None = None) -> TrackResult:
        self._require_time(at)
        if status not in ALLOWED[self._status]:
            raise DomainError(code=_CONFLICT_CODE, name=_CONFLICT_NAME, retryable=False)
        if status != self._status:
            self._since = at
            self._episode_id = self._next_episode(status)
        self._status = status
        self._detail = detail
        self._last_at = at
        return self._result(notify=self._maybe_notify())

    def set_focus(self, *, focused: bool, window_active: bool, at: datetime) -> TrackResult:
        self._require_time(at)
        self._focused = focused
        self._window_active = window_active
        self._last_at = at
        return self._result(notify=self._maybe_notify())

    def _next_episode(self, status: TabStatus) -> str | None:
        if status not in ATTENTION_STATUSES:
            return None
        self._episode_seq += 1
        return f"{status.value}:{self._episode_seq}"

    def _maybe_notify(self) -> bool:
        episode = self._episode_id
        if episode is None or episode in self._notified or not self.attention:
            return False
        self._notified.add(episode)
        return True

    def _require_time(self, at: datetime) -> None:
        if at.tzinfo is None:
            message = "event time must be timezone-aware"
            raise ValueError(message)
        if at < self._last_at:
            message = "event time must be monotonic"
            raise ValueError(message)

    def _result(self, *, notify: bool) -> TrackResult:
        return TrackResult(
            status=self._status,
            status_since=self._since,
            detail=self._detail,
            attention=self.attention,
            notify=notify,
            episode_id=self._episode_id,
        )
