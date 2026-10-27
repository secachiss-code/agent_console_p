"""Tab rows through the single writer. Secrets are not columns and are not written."""

from __future__ import annotations

from datetime import datetime
from typing import Final, final

from sqlalchemy import select
from sqlalchemy.orm import Session

from aconsole.core.application.ports.tab_store import StoredTab, TabDirectory
from aconsole.core.domain.agent import AgentKind
from aconsole.core.domain.tab_status import TabStatus
from aconsole.core.domain.workspace import WorkspaceMode
from aconsole.core.infrastructure.persistence.models import (
    AgentAccount,
    PlanNode,
    Project,
    Setting,
    Tab,
    Workspace,
)
from aconsole.core.infrastructure.persistence.writer import Writer

# One JSON document for APP_API Settings. `checkpoints_keep` stays its own key (the sweep reads it).
SETTINGS_KEY: Final = "app"
_NAME: Final = 120
_ACCOUNT: Final = 64
_WORKSPACE: Final = 26
_PLAN: Final = "local"
_BRANCH: Final = "main"
_BAD_RESULT: Final = "tab store returned an unexpected value"


def save_tab(session: Session, tab: StoredTab) -> None:
    """Upsert the project, its account, the workspace, and the tab. No token column is touched."""
    _upsert_project(session, tab)
    account_id = _upsert_account(session, tab)
    workspace_id = _upsert_workspace(session, tab)
    node_id = _live_node(session, tab.plan_node)
    _upsert_tab(session, tab, workspace_id=workspace_id, account_id=account_id, node_id=node_id)


def remove_tab(session: Session, tab_id: str, closed_at: datetime, *, drop_worktree: bool) -> None:
    """Keep the row for the retention sweep. A removed worktree is marked, not deleted here."""
    row = session.get(Tab, tab_id)
    if row is None:
        return
    row.closed_at = closed_at
    row.updated_at = closed_at
    if not drop_worktree or row.workspace_id is None:
        return
    workspace = session.get(Workspace, row.workspace_id)
    if workspace is None:
        return
    workspace.removed_at = closed_at
    workspace.locked = False


def load_open_tabs(session: Session) -> tuple[StoredTab, ...]:
    """Open tabs in display order. A row missing its project or workspace is skipped."""
    rows = session.scalars(select(Tab).where(Tab.closed_at.is_(None)).order_by(Tab.tab_order, Tab.id)).all()
    loaded: list[StoredTab] = []
    for row in rows:
        stored = _stored(session, row)
        if stored is not None:
            loaded.append(stored)
    return tuple(loaded)


def read_settings(session: Session) -> TabDirectory:
    """`worktrees_dir` from the settings document. Missing or blank means the host default."""
    row = session.get(Setting, SETTINGS_KEY)
    if row is None:
        return TabDirectory(worktrees_dir=None, rev=0)
    raw = row.value.get("worktrees_dir")
    directory = raw if isinstance(raw, str) and raw != "" else None
    return TabDirectory(worktrees_dir=directory, rev=row.rev)


@final
class TabDatabase:
    """`TabStore` backed by the writer thread. Call it from any thread except that one."""

    def __init__(self, writer: Writer) -> None:
        self._writer = writer

    def save(self, tab: StoredTab) -> None:
        _ = self._writer.call(lambda session: save_tab(session, tab))

    def remove(self, tab_id: str, *, closed_at: datetime, drop_worktree: bool) -> None:
        _ = self._writer.call(lambda session: remove_tab(session, tab_id, closed_at, drop_worktree=drop_worktree))

    def load_open(self) -> tuple[StoredTab, ...]:
        # The list is filled on the writer thread and read only after `call` returns.
        sink: list[StoredTab] = []

        def collect(session: Session) -> None:
            sink.extend(load_open_tabs(session))

        _ = self._writer.call(collect)
        return tuple(sink)

    def settings(self) -> TabDirectory:
        found = self._writer.call(read_settings)
        if not isinstance(found, TabDirectory):
            raise TypeError(_BAD_RESULT)
        return found


def _upsert_project(session: Session, tab: StoredTab) -> None:
    row = session.get(Project, tab.project_id)
    name = tab.project_name[:_NAME] or tab.project_id[:_NAME]
    branch = tab.base_branch or _BRANCH
    if row is None:
        session.add(
            Project(
                id=tab.project_id,
                name=name,
                root_path=tab.project_root,
                default_branch=branch,
                vault_folder=None,
                languages=[],
                created_at=tab.created_at,
            )
        )
        session.flush()
        return
    row.name = name
    row.root_path = tab.project_root
    row.default_branch = branch


def _upsert_account(session: Session, tab: StoredTab) -> str | None:
    if tab.container == "":
        return None
    account_id = f"{tab.agent}:{tab.project_id}"[:_ACCOUNT]
    row = session.get(AgentAccount, account_id)
    if row is None:
        session.add(
            AgentAccount(
                id=account_id,
                agent_kind=AgentKind(tab.agent),
                plan_label=_PLAN,
                container_name=tab.container,
            )
        )
        session.flush()
        return account_id
    row.container_name = tab.container
    row.agent_kind = AgentKind(tab.agent)
    return account_id


def _upsert_workspace(session: Session, tab: StoredTab) -> str:
    found = session.scalars(select(Workspace).where(Workspace.path == tab.worktree)).first()
    mode = WorkspaceMode(tab.workspace_mode)
    if found is None:
        workspace_id = f"ws-{tab.id}"[:_WORKSPACE]
        session.add(
            Workspace(
                id=workspace_id,
                project_id=tab.project_id,
                mode=mode,
                path=tab.worktree,
                branch=tab.branch,
                base_branch=tab.base_branch,
                base_commit=None,
                locked=True,
                dirty_cached=tab.dirty,
                created_at=tab.created_at,
                removed_at=None,
            )
        )
        session.flush()
        return workspace_id
    found.project_id = tab.project_id
    found.mode = mode
    found.branch = tab.branch
    found.base_branch = tab.base_branch
    found.locked = True
    found.dirty_cached = tab.dirty
    found.removed_at = None
    return found.id


def _live_node(session: Session, node_id: str | None) -> str | None:
    if node_id is None or session.get(PlanNode, node_id) is None:
        return None
    return node_id


def _upsert_tab(
    session: Session,
    tab: StoredTab,
    *,
    workspace_id: str,
    account_id: str | None,
    node_id: str | None,
) -> None:
    row = session.get(Tab, tab.id)
    if row is None:
        session.add(
            Tab(
                id=tab.id,
                project_id=tab.project_id,
                workspace_id=workspace_id,
                agent_kind=AgentKind(tab.agent),
                account_id=account_id,
                title=tab.title,
                tab_order=tab.order,
                last_status=TabStatus(tab.status),
                status_detail=tab.status_detail,
                plan_node_id=node_id,
                context_used_pct=None,
                session_cost_usd=None,
                agent_session_id=tab.session,
                closed_at=None,
                created_at=tab.created_at,
                updated_at=tab.since,
            )
        )
        return
    row.project_id = tab.project_id
    row.workspace_id = workspace_id
    row.agent_kind = AgentKind(tab.agent)
    row.account_id = account_id
    row.title = tab.title
    row.tab_order = tab.order
    row.last_status = TabStatus(tab.status)
    row.status_detail = tab.status_detail
    row.plan_node_id = node_id
    row.agent_session_id = tab.session
    row.closed_at = None
    row.updated_at = tab.since


def _stored(session: Session, row: Tab) -> StoredTab | None:
    if row.workspace_id is None:
        return None
    workspace = session.get(Workspace, row.workspace_id)
    project = session.get(Project, row.project_id)
    if workspace is None or project is None:
        return None
    account = None if row.account_id is None else session.get(AgentAccount, row.account_id)
    container = "" if account is None else account.container_name
    return StoredTab(
        id=row.id,
        project_id=row.project_id,
        project_name=project.name,
        project_root=project.root_path,
        container=container,
        agent=row.agent_kind.value,
        title=row.title,
        order=row.tab_order,
        status=row.last_status.value,
        status_detail=row.status_detail,
        since=row.updated_at,
        created_at=row.created_at,
        worktree=workspace.path,
        branch=workspace.branch,
        base_branch=workspace.base_branch,
        dirty=workspace.dirty_cached,
        workspace_mode=workspace.mode.value,
        plan_node=row.plan_node_id,
        session=row.agent_session_id,
    )
