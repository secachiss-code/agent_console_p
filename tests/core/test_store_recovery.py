"""Durable tab/settings recovery, partial updates, and failed-write isolation."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from aconsole.core.application.ports.tab_store import ProjectRecord, StoredTab, TabDirectory
from aconsole.core.infrastructure.persistence.engine import write_engine
from aconsole.core.infrastructure.persistence.models import Project, Setting, Tab, Workspace
from aconsole.core.infrastructure.persistence.repositories.tabs import SETTINGS_KEY, TabDatabase
from aconsole.core.infrastructure.persistence.startup import prepare_database, sqlite_url
from aconsole.core.infrastructure.persistence.writer import Writer

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session

_WHEN = datetime(2026, 11, 9, tzinfo=UTC)


@contextmanager
def _open(database: Path) -> Iterator[tuple[TabDatabase, Writer]]:
    engine = write_engine(sqlite_url(database))
    writer = Writer(engine)
    writer.start()
    try:
        yield TabDatabase(writer), writer
    finally:
        writer.stop()
        engine.dispose()


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "state.db"
    prepare_database(path, now=_WHEN)
    return path


def _tab(tab_id: str = "tab-one") -> StoredTab:
    return StoredTab(
        id=tab_id, project_id="project", project_name="Проект", project_root="/repo",
        container="box", agent="claude", title="Сессия", order=1, status="idle",
        status_detail="ready", since=_WHEN, created_at=_WHEN, worktree="/wt/one",
        branch="feature", base_branch="main", dirty=True, workspace_mode="new_worktree",
        plan_node=None, session="session-one", fork_pending=True, isolated=True,
    )


def test_empty_database_has_no_implicit_tabs_projects_or_setup(database: Path) -> None:
    with _open(database) as (store, _):
        assert store.load_open() == ()
        assert store.load_projects().projects == ()
        assert store.load_projects().active is None
        assert store.settings() == TabDirectory(worktrees_dir=None, rev=0)


def test_legacy_active_project_is_ignored_until_a_string_is_saved(database: Path) -> None:
    with _open(database) as (_, writer):
        writer.call(lambda session: session.add(Setting(
            key=SETTINGS_KEY, value={"active_project": 42, "theme": "dark"}, rev=3,
        )))
    with _open(database) as (store, _):
        assert store.load_projects().active is None
        store.save_active_project("selected")
        assert store.settings().rev == 3
        assert store.settings().preferences == {"theme": "dark"}
    with _open(database) as (store, _):
        assert store.load_projects().active == "selected"


def test_shared_workspace_tabs_restore_in_stable_order_and_keep_project_branch(database: Path) -> None:
    project = ProjectRecord(id="project", name="Old", root="/repo", default_branch="trunk", created_at=_WHEN)
    first = replace(_tab(), project_name="", base_branch=None, container="", plan_node="deleted-node")
    second = replace(first, id="tab-two", title="Second")
    with _open(database) as (store, _):
        store.save_project(project)
        store.save(second)
        store.save(first)
    with _open(database) as (store, writer):
        assert store.load_open() == (
            replace(first, project_name="project", plan_node=None),
            replace(second, project_name="project", plan_node=None),
        )
        assert store.load_projects().projects == (replace(project, name="project"),)

        def workspace_ids(session: Session) -> list[str]:
            return list(session.scalars(select(Workspace.id)))

        assert writer.call(workspace_ids) == ["ws-tab-two"]


@pytest.mark.parametrize("first", ["setup", "approval", "preferences", "settings", "active"])
def test_partial_settings_survive_restart_and_keep_unrelated_values(database: Path, first: str) -> None:
    """Every settings entry point can create the document before other writers merge into it."""
    with _open(database) as (store, _):
        if first == "setup":
            store.save_setup(completed=True, rev=1)
        elif first == "approval":
            store.save_approval_settings(mode="native", hold=60, rev=1)
        elif first == "preferences":
            store.save_preferences(values={"theme": "dark"}, rev=1)
        elif first == "settings":
            store.save_settings(TabDirectory(worktrees_dir="/custom", rev=1))
        else:
            store.save_active_project("project")
        store.save_preferences(values={"theme": "dark", "terminal_font_size": 17}, rev=2)
        store.save_setup(completed=True, rev=3)
        store.save_approval_settings(mode="native", hold=60, rev=4)
        store.save_active_project("project")
    with _open(database) as (store, _):
        assert store.settings() == TabDirectory(
            worktrees_dir="/custom" if first == "settings" else None, rev=4,
            setup_completed=True, approvals_mode="native", approval_hold_seconds=60,
            preferences={"theme": "dark", "terminal_font_size": 17},
        )
        assert store.load_projects().active == "project"
        store.save_settings(TabDirectory(
            worktrees_dir="/new", rev=5, setup_completed=True, approval_policy="deny",
            approval_dangerous="allow", preferences={"theme": "light"},
        ))
    with _open(database) as (store, _):
        assert store.load_projects().active == "project"
        assert store.settings() == TabDirectory(
            worktrees_dir="/new", rev=5, setup_completed=True, approval_policy="deny",
            approval_dangerous="allow", preferences={"theme": "light", "terminal_font_size": 17},
        )


@pytest.mark.parametrize("values", [
    {},
    {"worktrees_dir": "", "setup_completed": "yes", "approvals_mode": "unknown"},
    {"worktrees_dir": 12, "approval_hold_seconds": 29, "approval_policy": "unknown"},
    {"worktrees_dir": [], "approval_hold_seconds": 541, "approval_dangerous": "deny"},
    {"approval_hold_seconds": True},
    {"approval_hold_seconds": "60"},
])
def test_legacy_or_invalid_settings_have_safe_defaults_after_restart(database: Path, values: dict[str, object]) -> None:
    with _open(database) as (_, writer):
        writer.call(lambda session: session.add(Setting(key=SETTINGS_KEY, value=values, rev=7)))
    with _open(database) as (store, _):
        assert store.settings() == TabDirectory(worktrees_dir=None, rev=7)


@pytest.mark.parametrize("hold", [30, 540])
def test_approval_hold_boundaries_survive_restart(database: Path, hold: int) -> None:
    with _open(database) as (store, _):
        store.save_approval_settings(mode="native", hold=hold, rev=2)
    with _open(database) as (store, _):
        assert store.settings().approval_hold_seconds == hold
        assert store.settings().approvals_mode == "native"


@pytest.mark.parametrize("drop", [False, True])
def test_closed_tab_reopens_with_same_workspace_and_updated_account(database: Path, *, drop: bool) -> None:
    original = _tab()
    with _open(database) as (store, _):
        store.save(original)
        store.remove(original.id, closed_at=_WHEN, drop_worktree=drop)
        store.remove("absent", closed_at=_WHEN, drop_worktree=True)
    with _open(database) as (store, writer):
        assert store.load_open() == ()

        def workspace_state(session: Session) -> tuple[bool, datetime | None]:
            row = session.scalars(select(Workspace)).one()
            return row.locked, row.removed_at

        assert writer.call(workspace_state) == (not drop, _WHEN if drop else None)
        reopened = replace(original, title="Reopened", container="new-box", session="session-two", dirty=False)
        store.save(reopened)
    with _open(database) as (store, writer):
        assert store.load_open() == (reopened,)
        assert writer.call(workspace_state) == (True, None)


def test_workspace_deletion_hides_unrestorable_tab_but_preserves_other_tabs(database: Path) -> None:
    first = _tab()
    second = replace(_tab("tab-two"), worktree="/wt/two", order=2, container="")
    with _open(database) as (store, writer):
        store.save(first)
        store.save(second)

        def delete_workspace(session: Session) -> None:
            row = session.scalars(select(Workspace).where(Workspace.path == first.worktree)).one()
            session.delete(row)

        writer.call(delete_workspace)
        store.remove(first.id, closed_at=_WHEN, drop_worktree=True)
    with _open(database) as (store, _):
        assert store.load_open() == (second,)
        store.save(first)
    with _open(database) as (store, _):
        assert store.load_open() == (first, second)


def test_invalid_tab_save_rolls_back_project_account_and_workspace_changes(database: Path) -> None:
    original = _tab()
    with _open(database) as (store, writer):
        store.save(original)
        # Invalid enum conversion happens after project and account updates.
        with pytest.raises(ValueError, match="not a valid WorkspaceMode"):
            store.save(replace(original, project_name="lost", container="lost", workspace_mode="invalid"))
        # A new project is flushed before account enum validation fails.
        with pytest.raises(ValueError, match="not a valid AgentKind"):
            store.save(replace(original, id="bad", project_id="bad-project", project_root="/bad", agent="invalid"))
        assert writer.failed_jobs == 2
        assert store.load_open() == (original,)
        assert [row.id for row in store.load_projects().projects] == [original.project_id]
        store.save_preferences(values={"theme": "dark"}, rev=1)
    with _open(database) as (store, _):
        assert store.load_open() == (original,)
        assert store.settings().preferences == {"theme": "dark"}


def test_duplicate_project_failure_does_not_poison_later_transactions(database: Path) -> None:
    project = ProjectRecord(id="project", name="Original", root="/repo", default_branch="trunk", created_at=_WHEN)
    with _open(database) as (store, _):
        store.save_project(project)
        with pytest.raises(IntegrityError):
            store.save_project(replace(project, name="Duplicate"))
        assert store.save_project_policy("absent", "deny", "ask") is False
        assert store.save_project_policy(project.id, "deny", "allow") is True
        store.save_active_project(project.id)
    with _open(database) as (store, _):
        found = store.load_projects()
        assert found.projects == (replace(project, approval_policy="deny", approval_dangerous="allow"),)
        assert found.active == project.id
        assert store.save_project_policy(project.id, None, None) is True
        store.save_active_project(None)
    with _open(database) as (store, _):
        assert store.load_projects().projects == (project,)
        assert store.load_projects().active is None
        store.remove_project(project.id)
        store.remove_project("absent")
    with _open(database) as (store, _):
        assert store.load_projects().projects == ()


def test_deleting_a_project_cascades_its_tabs_without_affecting_another_project(database: Path) -> None:
    first = _tab()
    second = replace(
        _tab("tab-two"), project_id="other", project_name="Other", project_root="/other", worktree="/other",
    )
    with _open(database) as (store, _):
        store.save(first)
        store.save(second)
        store.remove_project(first.project_id)
    with _open(database) as (store, writer):
        assert store.load_open() == (second,)

        def remaining(session: Session) -> tuple[list[str], list[str], list[str]]:
            return (
                list(session.scalars(select(Project.id))),
                list(session.scalars(select(Tab.id))),
                list(session.scalars(select(Workspace.path))),
            )

        assert writer.call(remaining) == ([second.project_id], [second.id], [second.worktree])
