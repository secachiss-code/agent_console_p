"""In-memory snapshot the UI reads. Event handlers only update this model."""

from collections.abc import Callable
from typing import final

import msgspec

from aconsole.contracts.app import AppSnapshot
from aconsole.contracts.events import (
    AgentsChanged,
    ApprovalRequested,
    ApprovalResolved,
    PlanUpdated,
    ProjectsChanged,
    QuotaUpdated,
    ResyncRequired,
    SettingsChanged,
    SetupRequired,
    TabAdded,
    TabRemoved,
    TabUpdated,
    UiEvent,
)
from aconsole.contracts.ipc import EventEnvelope

CONNECTING_BANNER = "Подключение к ядру…"


class _EventKind(msgspec.Struct, frozen=True, kw_only=True):
    kind: str = msgspec.field(name="type")


def event_name(event: UiEvent) -> str:
    return msgspec.json.decode(msgspec.json.encode(event), type=_EventKind).kind


@final
class Subscription:
    """A live event handler. `close()` stops further calls; closing twice is harmless."""

    def __init__(
        self, listeners: list[Callable[[UiEvent, CoreStore], None]], callback: Callable[[UiEvent, CoreStore], None]
    ) -> None:
        self._listeners = listeners
        self._callback = callback
        self._active = True

    @property
    def active(self) -> bool:
        return self._active

    def close(self) -> None:
        if not self._active:
            return
        self._active = False
        if self._callback in self._listeners:
            self._listeners.remove(self._callback)


class CoreStore:
    """The latest snapshot plus the last contiguous event seq."""

    snapshot: AppSnapshot | None
    seq: int
    banner: str | None
    reconnecting: bool
    event_types: set[str]
    _listeners: list[Callable[[UiEvent, CoreStore], None]]
    _snapshot_listeners: list[Callable[[CoreStore], None]]

    def __init__(self) -> None:
        self.snapshot = None
        self.seq = 0
        self.banner = None
        self.reconnecting = False
        self.event_types = set()
        self._listeners = []
        self._snapshot_listeners = []

    def subscribe(self, callback: Callable[[UiEvent, CoreStore], None]) -> None:
        """Call `callback(event, store)` after every applied event, for the life of the store."""
        self._listeners.append(callback)

    def listen(self, callback: Callable[[UiEvent, CoreStore], None]) -> Subscription:
        """Like `subscribe`, and the returned `Subscription` can stop it."""
        self._listeners.append(callback)
        return Subscription(self._listeners, callback)

    def subscribe_snapshot(self, callback: Callable[[CoreStore], None]) -> None:
        """Listen for a snapshot that replaced the previous one.

        Initial load, resync, and reconnect all call ``apply_snapshot``. That
        notifies these callbacks with the store after ``snapshot`` is replaced.
        This is not a ``UiEvent`` and does not go through ``subscribe``.
        """
        self._snapshot_listeners.append(callback)

    def apply_snapshot(self, snapshot: AppSnapshot, seq: int) -> None:
        """Replace the snapshot, then notify ``subscribe_snapshot`` listeners."""
        self.snapshot = snapshot
        self.seq = seq
        self.banner = None
        self.reconnecting = False
        self._notify_snapshot()

    def apply(self, envelope: EventEnvelope) -> bool:
        """Apply one event. False means the UI must snapshot again."""
        self.event_types.add(event_name(envelope.event))
        if envelope.seq != self.seq + 1 or isinstance(envelope.event, ResyncRequired):
            self.banner = CONNECTING_BANNER
            self.reconnecting = True
            self._notify(envelope.event)
            return False
        self.seq = envelope.seq
        self._apply_tab(envelope.event)
        self._apply_model(envelope.event)
        self._notify(envelope.event)
        return True

    def _notify(self, event: UiEvent) -> None:
        for callback in tuple(self._listeners):
            callback(event, self)

    def _notify_snapshot(self) -> None:
        for callback in tuple(self._snapshot_listeners):
            callback(self)

    def _apply_tab(self, event: UiEvent) -> None:
        snapshot = self.snapshot
        if snapshot is None:
            return
        if isinstance(event, TabAdded):
            if any(tab.id == event.tab.id for tab in snapshot.tabs):
                replaced = tuple(event.tab if tab.id == event.tab.id else tab for tab in snapshot.tabs)
                self.snapshot = msgspec.structs.replace(snapshot, tabs=replaced)
                return
            self.snapshot = msgspec.structs.replace(snapshot, tabs=(*snapshot.tabs, event.tab))
            return
        if isinstance(event, TabRemoved):
            kept = tuple(tab for tab in snapshot.tabs if tab.id != event.tab_id)
            self.snapshot = msgspec.structs.replace(snapshot, tabs=kept)
            return
        if isinstance(event, TabUpdated):
            replaced = tuple(event.tab if tab.id == event.tab.id else tab for tab in snapshot.tabs)
            self.snapshot = msgspec.structs.replace(snapshot, tabs=replaced)

    def _apply_model(self, event: UiEvent) -> None:
        snapshot = self.snapshot
        if snapshot is None:
            return
        updated = _model(snapshot, event)
        if updated is not snapshot:
            self.snapshot = updated


def _put[Item](items: tuple[Item, ...], item: Item, key: Callable[[Item], object]) -> tuple[Item, ...]:
    item_key = key(item)
    if any(key(current) == item_key for current in items):
        return tuple(item if key(current) == item_key else current for current in items)
    return (*items, item)


def _model(snapshot: AppSnapshot, event: UiEvent) -> AppSnapshot:
    if isinstance(event, ApprovalRequested | ApprovalResolved):
        return _approvals(snapshot, event)
    if isinstance(event, QuotaUpdated | PlanUpdated):
        return _resources(snapshot, event)
    if isinstance(event, ProjectsChanged | AgentsChanged | SettingsChanged | SetupRequired):
        return _catalog(snapshot, event)
    return snapshot


def _approvals(snapshot: AppSnapshot, event: ApprovalRequested | ApprovalResolved) -> AppSnapshot:
    if isinstance(event, ApprovalRequested):
        approvals = _put(snapshot.approvals, event.approval, lambda item: item.id)
        return msgspec.structs.replace(snapshot, approvals=approvals)
    kept = tuple(item for item in snapshot.approvals if item.id != event.approval_id)
    return msgspec.structs.replace(snapshot, approvals=kept)


def _resources(snapshot: AppSnapshot, event: QuotaUpdated | PlanUpdated) -> AppSnapshot:
    if isinstance(event, QuotaUpdated):
        accounts = _put(snapshot.accounts, event.account, lambda item: item.id)
        return msgspec.structs.replace(snapshot, accounts=accounts)
    plans = _put(snapshot.plans, event.plan, lambda item: item.id)
    return msgspec.structs.replace(snapshot, plans=plans)


def _catalog(
    snapshot: AppSnapshot,
    event: ProjectsChanged | AgentsChanged | SettingsChanged | SetupRequired,
) -> AppSnapshot:
    if isinstance(event, ProjectsChanged):
        return msgspec.structs.replace(snapshot, projects=event.projects, active_project=event.active_project)
    if isinstance(event, AgentsChanged):
        return msgspec.structs.replace(snapshot, agents=event.agents)
    if isinstance(event, SettingsChanged):
        return msgspec.structs.replace(snapshot, settings_rev=event.rev)
    return msgspec.structs.replace(snapshot, setup=event.setup)
