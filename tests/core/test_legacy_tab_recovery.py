"""Close/restart pre-work-session tabs restored from an actual SQLite database."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from aconsole.contracts.app import CloseTabParams, FocusParams, RestartParams, StartedParams
from aconsole.contracts.common import TabId, TabStatus
from aconsole.contracts.events import TabRemoved, UiEvent
from aconsole.core.application.ports.tab_store import StoredTab
from aconsole.core.application.services.tabs import TabRequestError, TabService
from aconsole.core.domain.clock import FrozenClock
from aconsole.core.infrastructure.persistence.engine import write_engine
from aconsole.core.infrastructure.persistence.models import Workspace
from aconsole.core.infrastructure.persistence.repositories.tabs import TabDatabase
from aconsole.core.infrastructure.persistence.startup import prepare_database, sqlite_url
from aconsole.core.infrastructure.persistence.writer import Writer
from tests.core.conftest import FakeTabPorts

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_WHEN = datetime(2026, 11, 9, tzinfo=UTC)
_ID = TabId("legacy")


class _Ports(FakeTabPorts):
    def __init__(self) -> None:
        super().__init__()
        self.refuse_remove = False
        self.after_remove = "clean"
        self.fail_recheck = False

    def remove_worktree(self, repo: str, dest: str, *, discard_ignored: bool) -> bool:
        self.removed.append((repo, dest, discard_ignored))
        self.state = self.after_remove
        return not self.refuse_remove

    def worktree_state(self, repo: str, dest: str) -> str:
        if self.removed and self.fail_recheck:
            msg = "WORKTREE_DIRTY"
            raise TabRequestError(msg)
        return super().worktree_state(repo, dest)


def _row() -> StoredTab:
    return StoredTab(
        id=str(_ID), project_id="project", project_name="Project", project_root="/repo",
        container="box", agent="claude", title="Before upgrade", order=0, status="working",
        status_detail=None, since=_WHEN, created_at=_WHEN, worktree="/wt/legacy",
        branch="feature", base_branch="main", dirty=False, workspace_mode="new_worktree",
        plan_node=None, session="conversation",
    )


@contextmanager
def _database(path: Path) -> Iterator[tuple[TabDatabase, Writer]]:
    engine = write_engine(sqlite_url(path))
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
    with _database(path) as (store, _):
        store.save(_row())
        store.save(replace(_row(), id="neighbor", order=1, worktree="/wt/neighbor", status="idle"))
    return path


def _restore(store: TabDatabase, ports: _Ports, events: list[UiEvent]) -> TabService:
    service = TabService(FrozenClock(_WHEN), ports, listener=events.append)
    service.use_store(store)
    service.reload()
    assert service.snapshot().tabs[0].work_session_id is None
    assert service.snapshot().tabs[0].status is TabStatus.EXITED
    return service


def _assert_closed(database: Path, *, removed: bool) -> None:
    # A new engine/writer verifies durability, including the workspace kept for later recovery.
    with _database(database) as (store, writer):
        assert [row.id for row in store.load_open()] == ["neighbor"]
        rows = writer.call(lambda session: tuple(session.scalars(select(Workspace))))
        assert len(rows) == 2
        legacy = next(row for row in rows if row.path == "/wt/legacy")
        neighbor = next(row for row in rows if row.path == "/wt/neighbor")
        assert (legacy.removed_at is not None) is removed
        assert neighbor.removed_at is None


@pytest.mark.parametrize("state", ["dirty", "ignored", "ahead"])
def test_restored_dirty_tree_asks_before_stopping_and_survives_restart(database: Path, state: str) -> None:
    ports = _Ports()
    ports.state = state
    events: list[UiEvent] = []
    with _database(database) as (store, _):
        service = _restore(store, ports, events)
        result = service.close_tab(CloseTabParams(tab_id=_ID))
        assert not result.closed
        assert result.needs_decision
        assert result.dirty_summary == state
        assert ports.scans == ports.removed == ports.released == []
        assert not any(isinstance(event, TabRemoved) for event in events)
    with _database(database) as (store, _):
        rows = store.load_open()
        assert [row.id for row in rows] == ["legacy", "neighbor"]
        assert rows[0].dirty


@pytest.mark.parametrize("state", ["dirty", "ahead"])
def test_remove_cannot_discard_tracked_changes_in_a_restored_tree(database: Path, state: str) -> None:
    ports = _Ports()
    ports.state = state
    with _database(database) as (store, _):
        service = _restore(store, ports, [])
        with pytest.raises(TabRequestError, match="WORKTREE_DIRTY"):
            service.close_tab(CloseTabParams(tab_id=_ID, decision="remove"))
        assert ports.scans == ports.removed == []
        assert service.tab_ids() == ("legacy", "neighbor")
    with _database(database) as (store, _):
        assert len(store.load_open()) == 2


@pytest.mark.parametrize("state", ["clean", "dirty", "ignored"])
def test_keep_closes_restored_tab_and_preserves_its_workspace(database: Path, state: str) -> None:
    ports = _Ports()
    ports.state = state
    events: list[UiEvent] = []
    with _database(database) as (store, _):
        service = _restore(store, ports, events)
        service.focus(FocusParams(tab_id=_ID, window_active=True))
        result = service.close_tab(CloseTabParams(tab_id=_ID, decision="keep"))
        assert result.closed
        assert not result.needs_decision
        assert result.dirty_summary is None
        assert ports.released == ["/wt/legacy"]
        assert ports.removed == []
        assert events[-1] == TabRemoved(tab_id=_ID)
        assert service.tab_ids() == ("neighbor",)
    _assert_closed(database, removed=False)


@pytest.mark.parametrize(
    ("state", "decision", "discard"),
    [("clean", None, False), ("clean", "remove", False), ("ignored", "remove", True)],
)
def test_confirmed_removal_marks_only_the_restored_workspace_removed(
    database: Path, state: str, decision: str | None, *, discard: bool,
) -> None:
    ports = _Ports()
    ports.state = state
    with _database(database) as (store, _):
        service = _restore(store, ports, [])
        result = service.close_tab(CloseTabParams(tab_id=_ID, decision=decision))
        assert result.closed
        assert result.dirty_summary is None
        assert ports.removed == [("/repo", "/wt/legacy", discard)]
        assert ports.released == []
    _assert_closed(database, removed=True)


@pytest.mark.parametrize("after", ["clean", "dirty", "ignored", "unavailable"])
def test_git_remove_refusal_keeps_workspace_and_reports_fresh_state(database: Path, after: str) -> None:
    ports = _Ports()
    ports.refuse_remove = True
    ports.after_remove = after
    ports.fail_recheck = after == "unavailable"
    with _database(database) as (store, _):
        service = _restore(store, ports, [])
        result = service.close_tab(CloseTabParams(tab_id=_ID))
        assert result.closed
        assert result.dirty_summary == ("kept" if after in {"clean", "unavailable"} else after)
        assert len(ports.removed) == 1
    _assert_closed(database, removed=False)


@pytest.mark.parametrize("failure", ["scan", "kill", "survivor"])
def test_failed_orphan_cleanup_preserves_the_restored_workspace(database: Path, failure: str) -> None:
    ports = _Ports()
    if failure == "scan":
        ports.orphan_error = TabRequestError("CONTAINER_START_FAILED")
    else:
        ports.orphan_pids = (71,)
        if failure == "kill":
            ports.stop_error = TabRequestError("CONTAINER_START_FAILED")
        else:
            ports.after_stop = (71,)
    with _database(database) as (store, _):
        service = _restore(store, ports, [])
        result = service.close_tab(CloseTabParams(tab_id=_ID, decision="remove"))
        assert result.closed
        assert ports.removed == []
        assert ports.released == ["/wt/legacy"]
    _assert_closed(database, removed=False)


@pytest.mark.parametrize("mode", ["main_checkout", "existing_worktree"])
def test_closing_an_adopted_workspace_never_deletes_it(database: Path, mode: str) -> None:
    ports = _Ports()
    ports.state = "dirty"
    with _database(database) as (store, _):
        store.save(replace(_row(), workspace_mode=mode))
        service = _restore(store, ports, [])
        result = service.close_tab(CloseTabParams(tab_id=_ID, decision="remove"))
        assert result.closed
        assert not result.needs_decision
        assert ports.removed == ports.released == []
    _assert_closed(database, removed=False)


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("fork_pending", [False, True])
def test_restart_restored_tab_stops_old_process_before_persisting_new_state(
    database: Path, *, resume: bool, fork_pending: bool,
) -> None:
    ports = _Ports()
    ports.orphan_pids = (71,)
    with _database(database) as (store, _):
        store.save(replace(_row(), fork_pending=fork_pending))
        service = _restore(store, ports, [])
        service.started(StartedParams(tab_id=_ID, pid=72))
        result = service.restart(RestartParams(tab_id=_ID, resume=resume))
        assert not result.started
        assert result.status is TabStatus.IDLE
        # A pending fork hides its parent's id until the CLI reports the new conversation.
        assert result.agent_session_id == ("conversation" if resume and not fork_pending else None)
        assert ports.stopped == [72, 71]
        assert ports.pauses == [3.0]
        assert ports.removed == []
    with _database(database) as (store, _):
        row = store.load_open()[0]
        assert row.status == "idle"
        assert row.session == ("conversation" if resume else None)
        assert row.fork_pending is (resume and fork_pending)


def test_restart_failure_keeps_old_conversation_and_can_be_retried(database: Path) -> None:
    ports = _Ports()
    ports.orphan_error = TabRequestError("CONTAINER_START_FAILED")
    with _database(database) as (store, _):
        service = _restore(store, ports, [])
        service.started(StartedParams(tab_id=_ID, pid=72))
        with pytest.raises(TabRequestError, match="CONTAINER_START_FAILED"):
            service.restart(RestartParams(tab_id=_ID, resume=False))
        view = service.snapshot().tabs[0]
        assert not view.started
        assert view.agent_session_id == "conversation"
        ports.orphan_error = None
        result = service.restart(RestartParams(tab_id=_ID, resume=True))
        assert not result.started
        assert result.agent_session_id == "conversation"
        assert ports.stopped == [72]
    with _database(database) as (store, _):
        assert store.load_open()[0].session == "conversation"


@pytest.mark.parametrize(
    ("agent", "session", "error"),
    [("claude", None, "SESSION_NOT_FOUND"), ("cursor", "conversation", "AGENT_UNAVAILABLE")],
)
def test_unsupported_resume_leaves_restored_tab_and_workspace_untouched(
    database: Path, agent: str, session: str | None, error: str,
) -> None:
    ports = _Ports()
    with _database(database) as (store, _):
        store.save(replace(_row(), agent=agent, session=session))
        service = _restore(store, ports, [])
        with pytest.raises(TabRequestError, match=error):
            service.restart(RestartParams(tab_id=_ID, resume=True))
        assert ports.scans == ports.stopped == ports.removed == []
    with _database(database) as (store, _):
        rows = store.load_open()
        assert len(rows) == 2
        assert rows[0].agent == agent
        assert rows[0].session == session
