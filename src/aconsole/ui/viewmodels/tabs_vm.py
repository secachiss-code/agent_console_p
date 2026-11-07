"""Tab rows projected from the core store for the sidebar."""

from datetime import UTC, datetime
from typing import Literal, override

from gi.repository import Gio, GObject

from aconsole.contracts.app import (
    AccountView,
    CloseTabParams,
    CloseTabResult,
    EmptyResult,
    FocusParams,
    OpenTabParams,
    ProjectAddParams,
    ProjectIdParams,
    ProjectView,
    RenameParams,
    ReorderParams,
    TabView,
)
from aconsole.contracts.common import AgentKind, NodeId, ProjectId, TabId, WorkspaceMode
from aconsole.contracts.events import QuotaUpdated, TabAdded, TabRemoved, TabUpdated, UiEvent
from aconsole.contracts.git import GitStateResult
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.client.store import CoreStore
from aconsole.ui.gtk_typed import TypedListStore, gproperty
from aconsole.ui.viewmodels.git_vm import row_flags, tab_git_line
from aconsole.ui.viewmodels.logic.tab_format import format_tab

MAX_TAB_TITLE_LENGTH = 80


class TabItem(GObject.Object):
    """A sidebar row shares its title with the typed and GTK models."""

    tab_id: object = gproperty(str, "")
    project_id: object = gproperty(str, "")
    session_id: object = gproperty(str, "")
    title: object = gproperty(str, "")
    section_key: object = gproperty(str, "")
    section_title: object = gproperty(str, "")
    subtitle: object = gproperty(str, "")
    detail: object = gproperty(str, "")
    status_symbol: object = gproperty(str, "")
    status_code: object = gproperty(str, "")
    spinning: object = gproperty(bool, False)  # noqa: FBT003 -- GObject property default is positional
    attention: object = gproperty(bool, False)  # noqa: FBT003 -- GObject property default is positional
    waiting_minutes: object = gproperty(int, -1)
    git_line: object = gproperty(str, "")
    git_warning: object = gproperty(bool, False)  # noqa: FBT003 -- GObject property default is positional

    def __init__(self, tab_id: str, title: str) -> None:
        super().__init__()
        self.set_property("tab-id", tab_id)
        self.set_property("title", title)

    def apply(self, tab: TabView, accounts: tuple[AccountView, ...]) -> None:
        display = format_tab(tab, accounts, datetime.now(UTC))
        values: tuple[tuple[str, object], ...] = (
            ("session-id", tab.agent_session_id or ""),
            ("title", display.title), ("project-id", str(tab.project_id)), ("section-key", display.section_key),
            ("section-title", display.section_title), ("subtitle", display.subtitle),
            ("detail", display.detail), ("status-symbol", display.status_symbol),
            ("status-code", tab.status.value),
            ("spinning", display.spinning), ("attention", display.attention),
            ("waiting-minutes", display.waiting_minutes if display.waiting_minutes is not None else -1),
        )
        for name, value in values:
            if self.get_property(name) != value:
                self.set_property(name, value)


class TabListStore(TypedListStore[TabItem]):
    """Stable typed view of the same model used by GTK."""

    def __init__(self) -> None:
        super().__init__(TabItem)
        self.model: Gio.ListStore = Gio.ListStore.new(TabItem)

    @override
    def append(self, item: TabItem) -> None:
        self.model.append(item)

    @override
    def get(self, index: int) -> TabItem:
        item = self.model.get_item(index)
        if not isinstance(item, TabItem):
            message = "list store returned an unexpected item"
            raise TypeError(message)
        return item

    def replace(self, items: list[TabItem]) -> None:
        self.model.splice(0, self.model.get_n_items(), items)

    def get_n_items(self) -> int:
        return self.model.get_n_items()


class TabsViewModel:
    """One instance lives for the client lifetime; no private wrapper access."""

    def __init__(self, client: CoreClient) -> None:
        self.client: CoreClient = client
        self._store: TabListStore = TabListStore()
        self.model: Gio.ListStore = self._store.model
        self._refresh(client.store)
        client.store.subscribe(self._on_event)
        client.store.subscribe_snapshot(self._refresh)

    @property
    def store(self) -> TabListStore:
        return self._store

    def apply_git(self, state: GitStateResult) -> None:
        """Each of this project's tabs says where its worktree stands. The rows follow by notify."""
        rows = {
            str(tab): row for row in state.worktrees
            for tab in (row.tab_ids or (() if row.tab_id is None else (row.tab_id,)))
        }
        for at in range(self.model.get_n_items()):
            item = self.model.get_item(at)
            if not isinstance(item, TabItem) or str(item.project_id) != str(state.project_id):
                continue
            row = rows.get(str(item.tab_id))
            line = "" if row is None else tab_git_line(row)
            warning = row is not None and row_flags(row, state.base_branch).has_conflicts
            if str(item.git_line) != line:
                item.set_property("git-line", line)
            if bool(item.git_warning) != warning:
                item.set_property("git-warning", warning)

    def _refresh(self, state: CoreStore) -> None:
        snapshot = state.snapshot
        items: list[TabItem] = []
        if snapshot is not None:
            for tab in sorted(snapshot.tabs, key=lambda tab: tab.order):
                item = TabItem(str(tab.id), tab.title)
                item.apply(tab, snapshot.accounts)
                items.append(item)
        self._store.replace(items)

    def _on_event(self, event: UiEvent, state: CoreStore) -> None:
        if state.reconnecting:
            return
        if isinstance(event, TabUpdated) and self._retitle(state):
            return
        if isinstance(event, QuotaUpdated):
            _ = self._retitle(state)
            return
        if isinstance(event, (TabAdded, TabRemoved, TabUpdated)):
            self._refresh(state)

    def _retitle(self, state: CoreStore) -> bool:
        """Keep the same row objects when only titles change. A new id or order rebuilds the list."""
        snapshot = state.snapshot
        if snapshot is None:
            return False
        ordered = sorted(snapshot.tabs, key=lambda tab: tab.order)
        count = self._store.get_n_items()
        if count != len(ordered):
            return False
        rows = [self._store.get(index) for index in range(count)]
        if [row.get_property("tab-id") for row in rows] != [str(tab.id) for tab in ordered]:
            return False
        for row, tab in zip(rows, ordered, strict=True):
            row.apply(tab, snapshot.accounts)
        return True

    def refresh_times(self) -> None:
        """Update elapsed waiting time without replacing rows or selection."""
        _ = self._retitle(self.client.store)

    async def close(self, tab_id: str, decision: Literal["keep", "remove"] | None = None) -> CloseTabResult:
        return await self.client.request(
            "tab/close", CloseTabParams(tab_id=TabId(tab_id), decision=decision), CloseTabResult,
        )

    def can_fork(self, tab_id: str | None) -> bool:
        snapshot = self.client.store.snapshot
        tab = None if snapshot is None else next((row for row in snapshot.tabs if str(row.id) == tab_id), None)
        agent = None if snapshot is None or tab is None else next(
            (row for row in snapshot.agents if row.kind is tab.agent), None,
        )
        return tab is not None and bool(tab.agent_session_id) and agent is not None and "fork" in agent.capabilities

    async def open(  # noqa: PLR0913 -- explicit workspace and task context
        self, project_id: ProjectId, agent: AgentKind, *,
        workspace: WorkspaceMode = WorkspaceMode.NEW_WORKTREE, existing_worktree: str | None = None,
        work_session_id: str | None = None, isolated: bool = False, title: str | None = None,
        resume_session: str | None = None,
        base_branch: str | None = None, plan_node: NodeId | None = None,
    ) -> TabView:
        return await self.client.request(
            "tab/open",
            OpenTabParams(
                project_id=project_id, agent=agent, workspace=workspace, existing_worktree=existing_worktree,
                work_session_id=work_session_id, isolated=isolated, title=title, resume_session=resume_session,
                base_branch=base_branch, plan_node=plan_node,
            ),
            TabView,
        )

    async def set_active_project(self, project_id: ProjectId) -> None:
        _ = await self.client.request("project/setActive", ProjectIdParams(project_id=project_id), EmptyResult)

    async def add_project(self, root: str) -> ProjectView:
        return await self.client.request("project/add", ProjectAddParams(root=root), ProjectView)

    async def remove_project(self, project_id: ProjectId) -> None:
        _ = await self.client.request("project/remove", ProjectIdParams(project_id=project_id), EmptyResult)

    async def rename(self, tab_id: str, title: str) -> None:
        if not title.strip() or len(title) > MAX_TAB_TITLE_LENGTH:
            message = "tab title must contain 1 to 80 characters"
            raise ValueError(message)
        try:
            _ = await self.client.request("tab/rename", RenameParams(tab_id=TabId(tab_id), title=title), EmptyResult)
        except Exception:
            self._refresh(self.client.store)
            raise

    async def focus(self, tab_id: str | None, *, window_active: bool) -> None:
        _ = await self.client.request(
            "tab/focus",
            FocusParams(tab_id=TabId(tab_id) if tab_id is not None else None, window_active=window_active),
            EmptyResult,
        )

    async def reorder(self, tab_id: str, order: int) -> None:
        snapshot = self.client.store.snapshot
        if snapshot is None:
            message = "tab snapshot is unavailable"
            raise ValueError(message)
        ordered = sorted(snapshot.tabs, key=lambda tab: tab.order)
        source = next((tab for tab in ordered if str(tab.id) == tab_id), None)
        if source is None or not 0 <= order < len(ordered):
            message = "invalid tab reorder"
            raise ValueError(message)
        desired = [tab for tab in ordered if tab.id != source.id]
        desired.insert(order, source)
        if desired == ordered:
            return
        positions = [tab.order for tab in ordered]
        assignments = list(zip(desired, positions, strict=True))
        if ordered.index(source) > order:
            assignments.reverse()
        try:
            _ = await self.client.request(
                "tab/reorder", ReorderParams(tab_id=source.id, order=max(positions) + 1), EmptyResult,
            )
            for tab, position in assignments:
                if tab.id != source.id and tab.order != position:
                    _ = await self.client.request(
                        "tab/reorder", ReorderParams(tab_id=tab.id, order=position), EmptyResult,
                    )
            _ = await self.client.request(
                "tab/reorder", ReorderParams(tab_id=source.id, order=positions[order]), EmptyResult,
            )
        except Exception:
            self._refresh(self.client.store)
            raise
