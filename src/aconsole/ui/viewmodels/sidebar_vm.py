"""Active project plan and account limits projected from the core store."""

from collections.abc import Callable

from aconsole.contracts.app import AccountView, PlanView, TabView
from aconsole.contracts.events import UiEvent
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.client.store import CoreStore


class SidebarSummaryViewModel:
    def __init__(self, client: CoreClient) -> None:
        self.plan: PlanView | None = None
        self.accounts: tuple[AccountView, ...] = ()
        self.tabs: tuple[TabView, ...] = ()
        self.on_changed: Callable[[], None] | None = None
        self._refresh(client.store)
        client.store.subscribe(self._event)
        client.store.subscribe_snapshot(self._refresh)

    def _event(self, _event: UiEvent, store: CoreStore) -> None:
        if not store.reconnecting:
            self._refresh(store)

    def _refresh(self, store: CoreStore) -> None:
        snapshot = store.snapshot
        self.plan = None if snapshot is None else next((
            plan for plan in snapshot.plans
            if plan.project_id == snapshot.active_project and plan.status == "active"
        ), None)
        self.accounts = snapshot.accounts if snapshot is not None else ()
        self.tabs = snapshot.tabs if snapshot is not None else ()
        if self.on_changed is not None:
            self.on_changed()
