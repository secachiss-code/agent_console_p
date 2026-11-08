"""Session transfers and legacy history remain explainable across disk failures and restarts."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import msgspec
from sqlalchemy import MetaData, Table
from sqlalchemy.orm import Session

from aconsole.contracts.app import ProjectAddParams, ProjectView, TabIdParams, TabView, TerminalLaunchSpec
from aconsole.contracts.common import WorkspaceMode
from aconsole.contracts.work_sessions import WorkSessionHandoffParams, WorkSessionHistoryResult, WorkSessionParams
from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from aconsole.core.infrastructure.persistence.models import Workspace
from tests.core.git_helpers import commit, git, new_repo
from tests.core.test_wire import _connect, _daemon, _open, _stop

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_a_failed_handoff_journal_does_not_stop_the_old_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a", "base\n", "first")
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        first = await client.call("tab/open", _open(project.id), TabView)
        second = await client.call(
            "tab/open",
            msgspec.structs.replace(
                _open(project.id),
                work_session_id=first.work_session_id,
            ),
            TabView,
        )
        _ = await client.call("tab/launchSpec", TabIdParams(tab_id=first.id), TerminalLaunchSpec)
        history = daemon.tabs.history
        assert history is not None
        store = history._store
        assert store is not None
        reservation = daemon.tabs._tabs[str(first.id)].sidecar

        def fail(_record: object) -> None:
            message = "simulated full disk"
            raise OSError(message)

        monkeypatch.setattr(store, "save", fail)
        assert first.work_session_id is not None
        assert (
            await client.error(
                "workSession/handoff",
                WorkSessionHandoffParams(
                    project_id=project.id,
                    work_session_id=first.work_session_id,
                    to_tab=second.id,
                ),
            )
            == "DATABASE_TRANSACTION_FAILED"
        )
        assert daemon.tabs._tabs[str(first.id)].sidecar == reservation
        assert len(history.records(first.work_session_id)) == 1
        assert await client.error("tab/launchSpec", TabIdParams(tab_id=second.id)) == "WORKTREE_BUSY"
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_interrupted_handoffs_recover_refs_without_starting_agents(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        head = commit(repo, "a", "base\n", "first")
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        tab = await client.call("tab/open", _open(project.id), TabView)
        assert tab.work_session_id is not None
        record = daemon.tabs.work_sessions.require(str(project.id), tab.work_session_id)
        history = daemon.tabs.history
        assert history is not None
        missing = await daemon._in_worker(lambda: history.prepare(record, str(tab.id), "handoff", "before capture"))
        present = await daemon._in_worker(lambda: history.prepare(record, str(tab.id), "handoff", "after capture"))
        _ = git(repo, "update-ref", present.ref, head)
        await _stop(daemon, task, client)
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        restored = await client.call(
            "workSession/history",
            WorkSessionParams(
                project_id=project.id,
                work_session_id=record.id,
            ),
            WorkSessionHistoryResult,
        )
        assert restored.snapshots[missing.sequence - 1].status == "interrupted"
        assert restored.snapshots[present.sequence - 1].status == "finished"
        assert restored.snapshots[present.sequence - 1].oid == head
        assert not any(tab.started for tab in (await client.snapshot()).tabs)
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_legacy_checkpoints_are_adopted_without_rewriting_refs(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        head = commit(repo, "a", "base\n", "first")
        ref = "refs/aconsole/checkpoints/old-tab/1"
        _ = git(repo, "update-ref", ref, head)
        database = tmp_path / "aconsole.db"
        url = f"sqlite:///{database}"
        migrate(url, "0004_git_operations")
        engine = write_engine(url)
        now = datetime(2026, 11, 6, tzinfo=UTC)
        with Session(engine) as db:
            legacy_projects = Table("projects", MetaData(), autoload_with=engine)
            db.execute(legacy_projects.insert().values(
                id="p", name="p", root_path=str(repo), default_branch="main", languages=[], created_at=now.isoformat(),
            ))
            db.flush()
            db.add(
                Workspace(
                    id="w",
                    project_id="p",
                    mode=WorkspaceMode.MAIN_CHECKOUT,
                    path=str(repo),
                    branch="trunk",
                    base_branch="trunk",
                    base_commit=head,
                    locked=False,
                    dirty_cached=False,
                    created_at=now,
                    removed_at=None,
                )
            )
            db.commit()
        with engine.begin() as connection:
            metadata = MetaData()
            tabs = Table("tabs", metadata, autoload_with=connection)
            checkpoints = Table("checkpoints", metadata, autoload_with=connection)
            connection.execute(
                tabs.insert().values(
                    id="old-tab",
                    project_id="p",
                    workspace_id="w",
                    agent_kind="claude",
                    title="Old actor",
                    tab_order=0,
                    last_status="exited",
                    agent_session_id="native-conversation",
                    created_at=now.isoformat(),
                    updated_at=now.isoformat(),
                )
            )
            connection.execute(
                checkpoints.insert().values(
                    id="old-point",
                    tab_id="old-tab",
                    turn=1,
                    git_ref=ref,
                    tree_sha=git(repo, "rev-parse", "HEAD^{tree}"),
                    label="Old point",
                    files_changed=0,
                    insertions=0,
                    deletions=0,
                    created_at=now.isoformat(),
                )
            )
        engine.dispose()
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        tab = (await client.snapshot()).tabs[0]
        assert tab.agent_session_id == "native-conversation"
        assert tab.work_session_id is not None
        history = await client.call(
            "workSession/history",
            WorkSessionParams(
                project_id=tab.project_id,
                work_session_id=tab.work_session_id,
            ),
            WorkSessionHistoryResult,
        )
        assert len(history.snapshots) == 1
        point = history.snapshots[0]
        assert point.kind == "legacy_checkpoint"
        assert point.ref == ref
        assert point.oid == head
        assert point.actor_title == "Old actor"
        assert git(repo, "rev-parse", ref) == head
        await _stop(daemon, task, client)

    asyncio.run(scenario())
