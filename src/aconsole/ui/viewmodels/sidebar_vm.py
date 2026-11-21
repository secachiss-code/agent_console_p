"""Active project plan and account limits projected from the core store."""

import asyncio
from collections.abc import Callable

from aconsole.contracts.app import AccountView, PlanIdParams, PlanInboxEntry, PlanInboxResult, PlanView, TabView
from aconsole.contracts.events import PlanAttention, UiEvent
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.client.store import CoreStore


class SidebarSummaryViewModel:
    def __init__(self, client: CoreClient) -> None:
        self._client: CoreClient = client
        self.inbox: tuple[PlanInboxEntry, ...] = ()
        self.inbox_error: str | None = None
        self._inbox_task: asyncio.Task[None] | None = None
        self._plan_task: asyncio.Task[None] | None = None
        self._requested_plan: PlanView | None = None
        self.plan: PlanView | None = None
        self.accounts: tuple[AccountView, ...] = ()
        self.tabs: tuple[TabView, ...] = ()
        self.on_changed: Callable[[], None] | None = None
        self._refresh(client.store)
        client.store.subscribe(self._event)
        client.store.subscribe_snapshot(self._refresh)

    def _event(self, _event: UiEvent, store: CoreStore) -> None:
        if not store.reconnecting:
            if isinstance(_event, PlanAttention):
                self._requested_plan = None
            self._refresh(store)

    def _refresh(self, store: CoreStore) -> None:
        snapshot = store.snapshot
        previous = self.plan
        self.plan = (
            None
            if snapshot is None
            else next(
                (
                    plan
                    for plan in snapshot.plans
                    if plan.project_id == snapshot.active_project and plan.status == "active"
                ),
                None,
            )
        )
        if self.plan is None or previous is None or self.plan.id != previous.id:
            self.inbox = ()
            self.inbox_error = None
            self._requested_plan = None
        self._schedule_inbox()
        self.accounts = snapshot.accounts if snapshot is not None else ()
        self.tabs = snapshot.tabs if snapshot is not None else ()
        if self.on_changed is not None:
            self.on_changed()

    def refresh_plan(self) -> None:
        """Opening a graph reconciles its cached snapshot with the core."""
        if self.plan is None or self._plan_task is not None:
            return
        self._plan_task = asyncio.get_running_loop().create_task(self._load_plan(self.plan))

    async def _load_plan(self, plan: PlanView) -> None:
        try:
            await self.reload_plan(plan)
        except Exception as error:  # noqa: BLE001 -- keep stale data visible with an explicit refresh error
            if self.plan is not None and self.plan.id == plan.id:
                self.inbox_error = str(error)
                if self.on_changed is not None:
                    self.on_changed()
        finally:
            self._plan_task = None

    async def reload_plan(self, plan: PlanView) -> None:
        result = await self._client.request("plan/get", PlanIdParams(plan_id=plan.id), PlanView)
        self._client.store.update_plan(result)

    def refresh_inbox(self) -> None:
        self._requested_plan = None
        self._schedule_inbox()

    def _schedule_inbox(self) -> None:
        if self.plan is None or self.plan == self._requested_plan or self._inbox_task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._requested_plan = self.plan
        self._inbox_task = loop.create_task(self._load_inbox(self.plan))

    async def _load_inbox(self, plan: PlanView) -> None:
        try:
            result = await self._client.request("plan/inbox", PlanIdParams(plan_id=plan.id), PlanInboxResult)
            if self.plan is not None and self.plan.id == plan.id:
                self.inbox, self.inbox_error = result.entries, None
        except Exception as error:  # noqa: BLE001 -- keep request errors visible in the plan page
            if self.plan is not None and self.plan.id == plan.id:
                self.inbox_error = str(error)
        finally:
            self._inbox_task = None
            if self.on_changed is not None:
                self.on_changed()
            self._schedule_inbox()
