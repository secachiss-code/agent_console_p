"""Task workspaces survive their tabs and never become native CLI session ids."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import msgspec
from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session

from aconsole.contracts.app import (
    CloseTabParams,
    CloseTabResult,
    EmptyResult,
    ProjectAddParams,
    ProjectView,
    TabIdParams,
    TabView,
    TerminalLaunchSpec,
)
from aconsole.contracts.common import WorkspaceMode
from aconsole.contracts.git import (
    CleanupWorktreeParams,
    CleanupWorktreeResult,
    GitOperationResult,
    MergePreviewParams,
    MergePreviewResult,
    MergeWorktreeParams,
)
from aconsole.contracts.work_sessions import (
    WorkSessionCleanupWorkerParams,
    WorkSessionCreateParams,
    WorkSessionHandoffParams,
    WorkSessionHistoryResult,
    WorkSessionIntegrateParams,
    WorkSessionIntegratePreviewParams,
    WorkSessionListParams,
    WorkSessionListResult,
    WorkSessionParams,
    WorkSessionRestoreParams,
    WorkSessionRestorePreview,
    WorkSessionRestorePreviewParams,
    WorkSessionResultParams,
    WorkSessionView,
)
from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from aconsole.core.infrastructure.persistence.models import Project, WorkSessionRow, Workspace
from tests.core.git_helpers import commit, git, new_repo
from tests.core.test_wire import _connect, _daemon, _open, _stop


def test_a_task_survives_tabs_and_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo", branch="develop")
        _ = commit(repo, "a", "one\n", "first")
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        session = await client.call(
            "workSession/create",
            WorkSessionCreateParams(
                project_id=project.id,
                title="Auth task",
            ),
            WorkSessionView,
        )
        assert session.branch is not None
        assert session.branch.startswith("ac/s-Auth-task-")
        assert session.base_branch == "develop"
        first = await client.call(
            "tab/open",
            msgspec.structs.replace(
                _open(project.id),
                work_session_id=session.id,
            ),
            TabView,
        )
        second = await client.call(
            "tab/open",
            msgspec.structs.replace(
                _open(project.id),
                work_session_id=session.id,
            ),
            TabView,
        )
        assert first.worktree_path == second.worktree_path == session.worktree
        assert first.work_session_id == second.work_session_id == session.id
        assert first.agent_session_id is None
        result = await client.call("tab/close", CloseTabParams(tab_id=first.id), CloseTabResult)
        assert result.closed
        assert not result.needs_decision
        assert git(repo, "rev-parse", session.branch)
        assert (
            await client.error(
                "workSession/get",
                WorkSessionParams(
                    project_id="other",
                    work_session_id=session.id,
                ),
            )
            == "WORK_SESSION_NOT_FOUND"
        )
        await _stop(daemon, task, client)

        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        restored = await client.call(
            "workSession/list",
            WorkSessionListParams(
                project_id=project.id,
            ),
            WorkSessionListResult,
        )
        assert restored.sessions == (session,)
        snapshot = await client.snapshot()
        assert [tab.work_session_id for tab in snapshot.tabs] == [session.id]
        archived = await client.call(
            "workSession/close",
            WorkSessionParams(
                project_id=project.id,
                work_session_id=session.id,
            ),
            WorkSessionView,
        )
        assert archived.status == "archived"
        assert (
            await client.error(
                "tab/open",
                msgspec.structs.replace(
                    _open(project.id),
                    work_session_id=session.id,
                ),
            )
            == "WORK_SESSION_ARCHIVED"
        )
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_worker_results_reject_dirty_stale_and_unaccepted_cleanup(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a", "base\n", "first")
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        session = await client.call(
            "workSession/create", WorkSessionCreateParams(project_id=project.id, title="Concurrent task"),
            WorkSessionView,
        )
        workers = [await client.call(
            "tab/open", msgspec.structs.replace(_open(project.id), work_session_id=session.id, isolated=True),
            TabView,
        ) for _ in range(2)]
        points = []
        for number, worker in enumerate(workers):
            # Both launch reservations coexist because the physical workspaces differ.
            _ = await client.call("tab/launchSpec", TabIdParams(tab_id=worker.id), TerminalLaunchSpec)
            _ = commit(Path(worker.worktree_path), f"w{number}", "result\n", "worker result")
        for worker in workers:
            history = await client.call(
                "workSession/result", WorkSessionResultParams(
                    project_id=project.id, work_session_id=session.id, tab_id=worker.id,
                ), WorkSessionHistoryResult,
            )
            points.append(history.snapshots[-1])
        cleanup = WorkSessionCleanupWorkerParams(
            project_id=project.id, work_session_id=session.id, sequence=points[0].sequence,
        )
        assert await client.error("workSession/cleanupWorker", cleanup) == "WORKTREE_NOT_REMOVABLE"
        previews = [await client.call(
            "workSession/integratePreview", WorkSessionIntegratePreviewParams(
                project_id=project.id, work_session_id=session.id, sequence=point.sequence,
            ), MergePreviewResult,
        ) for point in points]
        dirty = Path(session.worktree) / "outside"
        _ = dirty.write_text("external edit\n", encoding="utf-8")
        assert await client.error(
            "workSession/integratePreview", WorkSessionIntegratePreviewParams(
                project_id=project.id, work_session_id=session.id, sequence=points[0].sequence,
            ),
        ) == "WORKTREE_DIRTY"
        await asyncio.to_thread(dirty.unlink)
        first, stale = [WorkSessionIntegrateParams(
            project_id=project.id, work_session_id=session.id, sequence=point.sequence,
            source_oid=preview.source_oid, target_oid=preview.target_oid, confirm_token=preview.confirm_token,
        ) for point, preview in zip(points, previews, strict=True)]
        _ = await client.call("workSession/integrate", first, GitOperationResult)
        assert await client.error("workSession/integrate", stale) == "MERGE_PREVIEW_STALE"
        # A later committed worker change must not disappear during cleanup of an older accepted result.
        _ = commit(Path(workers[0].worktree_path), "later", "unaccepted\n", "later work")
        assert await client.error("workSession/cleanupWorker", cleanup) == "WORKTREE_NOT_REMOVABLE"
        assert (Path(workers[0].worktree_path) / "later").read_text("utf-8") == "unaccepted\n"
        fresh = await client.call(
            "workSession/integratePreview", WorkSessionIntegratePreviewParams(
                project_id=project.id, work_session_id=session.id, sequence=points[1].sequence,
            ), MergePreviewResult,
        )
        _ = await client.call("workSession/integrate", msgspec.structs.replace(
            stale, source_oid=fresh.source_oid, target_oid=fresh.target_oid, confirm_token=fresh.confirm_token,
        ), GitOperationResult)
        _ = await client.call(
            "workSession/close", WorkSessionParams(project_id=project.id, work_session_id=session.id),
            WorkSessionView,
        )
        _ = await client.call("workSession/cleanupWorker", msgspec.structs.replace(
            cleanup, sequence=points[1].sequence,
        ), EmptyResult)
        assert not await asyncio.to_thread(Path(workers[1].worktree_path).exists)
        assert git(repo, "rev-parse", points[1].ref) == points[1].oid
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_migration_keeps_closed_workspaces_and_groups_path_aliases(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'old.db'}"
    migrate(url, "0004_git_operations")
    engine = write_engine(url)
    now = datetime(2026, 11, 6, tzinfo=UTC)
    root = tmp_path / "workspace"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with Session(engine) as database:
        database.add(Project(id="p", name="p", root_path=str(root), languages=[], created_at=now))
        database.flush()
        database.add_all(
            Workspace(
                id=f"w{number}",
                project_id="p",
                mode=WorkspaceMode.EXISTING_WORKTREE,
                path=str(path),
                branch="develop",
                base_branch="develop",
                base_commit=None,
                locked=False,
                dirty_cached=False,
                created_at=now,
                removed_at=None,
            )
            for number, path in enumerate((root, alias))
        )
        database.commit()
    engine.dispose()
    migrate(url, "head")
    engine = write_engine(url)
    try:
        with Session(engine) as database:
            rows = database.scalars(select(WorkSessionRow)).all()
            assert len(rows) == 1
            assert rows[0].worktree == str(root)
            assert rows[0].workspace_mode == "existing_worktree"
            assert rows[0].created_at == now
        # Reopening an already upgraded database is harmless and creates no Git metadata.
        migrate(url, "head")
        assert not (root / ".git").exists()
        with engine.connect() as connection:
            table = Table("workspaces", MetaData(), autoload_with=connection)
            assert len(connection.execute(select(table)).all()) == 2
    finally:
        engine.dispose()


def test_handoff_is_explicit_and_restore_preserves_head_and_index(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a", "before\n", "first")
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        first = await client.call(
            "tab/open",
            msgspec.structs.replace(
                _open(project.id),
                workspace=WorkspaceMode.NEW_WORKTREE,
            ),
            TabView,
        )
        assert first.work_session_id is not None
        second = await client.call(
            "tab/open",
            msgspec.structs.replace(
                _open(project.id),
                work_session_id=first.work_session_id,
            ),
            TabView,
        )
        _ = await client.call("tab/launchSpec", TabIdParams(tab_id=first.id), TerminalLaunchSpec)
        assert await client.error("tab/launchSpec", TabIdParams(tab_id=second.id)) == "WORKTREE_BUSY"
        tree = tmp_path / "wt" / repo.name / str(first.id)
        (tree / "a").write_text("after\n", encoding="utf-8")
        _ = git(tree, "add", "a")
        index, head = git(tree, "write-tree"), git(tree, "rev-parse", "HEAD")
        _ = await client.call(
            "workSession/handoff",
            WorkSessionHandoffParams(
                project_id=project.id,
                work_session_id=first.work_session_id,
                to_tab=second.id,
            ),
            TabView,
        )
        _ = await client.call("tab/launchSpec", TabIdParams(tab_id=second.id), TerminalLaunchSpec)
        history = await client.call(
            "workSession/history",
            WorkSessionParams(
                project_id=project.id,
                work_session_id=first.work_session_id,
            ),
            WorkSessionHistoryResult,
        )
        assert [point.kind for point in history.snapshots] == ["begin", "handoff", "begin"]
        assert history.snapshots[1].actor_tab_id == first.id
        preview = await client.call(
            "workSession/restorePreview",
            WorkSessionRestorePreviewParams(
                project_id=project.id,
                work_session_id=first.work_session_id,
                sequence=1,
            ),
            WorkSessionRestorePreview,
        )
        restored = await client.call(
            "workSession/restore",
            WorkSessionRestoreParams(
                project_id=project.id,
                work_session_id=first.work_session_id,
                sequence=1,
                confirm_token=preview.confirm_token,
            ),
            WorkSessionHistoryResult,
        )
        assert (tree / "a").read_text(encoding="utf-8") == "before\n"
        assert git(tree, "write-tree") == index
        assert git(tree, "rev-parse", "HEAD") == head
        assert restored.snapshots[-1].kind == "safety"
        assert (
            await client.error(
                "workSession/restore",
                WorkSessionRestoreParams(
                    project_id=project.id,
                    work_session_id=first.work_session_id,
                    sequence=1,
                    confirm_token=preview.confirm_token,
                ),
            )
            == "CONFIRM_TOKEN_INVALID"
        )
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_isolated_result_is_accepted_once_and_survives_tab_closure(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a", "base\n", "first")
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        session = await client.call(
            "workSession/create",
            WorkSessionCreateParams(
                project_id=project.id,
                title="Parallel work",
            ),
            WorkSessionView,
        )
        worker = await client.call(
            "tab/open",
            msgspec.structs.replace(
                _open(project.id),
                work_session_id=session.id,
                isolated=True,
            ),
            TabView,
        )
        assert worker.isolated
        assert worker.worktree_path != session.worktree
        assert git(Path(worker.worktree_path), "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
        _ = commit(Path(worker.worktree_path), "worker", "result\n", "worker commit")
        result = await client.call(
            "workSession/result",
            WorkSessionResultParams(
                project_id=project.id,
                work_session_id=session.id,
                tab_id=worker.id,
            ),
            WorkSessionHistoryResult,
        )
        point = result.snapshots[-1]
        assert point.kind == "result"
        preview = await client.call(
            "workSession/integratePreview",
            WorkSessionIntegratePreviewParams(
                project_id=project.id,
                work_session_id=session.id,
                sequence=point.sequence,
            ),
            MergePreviewResult,
        )
        params = WorkSessionIntegrateParams(
            project_id=project.id,
            work_session_id=session.id,
            sequence=point.sequence,
            source_oid=preview.source_oid,
            target_oid=preview.target_oid,
            confirm_token=preview.confirm_token,
        )
        accepted = await client.call("workSession/integrate", params, GitOperationResult)
        assert accepted.operation.status == "finished", accepted.operation.message
        assert accepted.operation.work_session_id == session.id
        assert (Path(session.worktree) / "worker").read_text(encoding="utf-8") == "result\n"
        again = await client.call("workSession/integrate", params, GitOperationResult)
        assert again.operation.id == accepted.operation.id
        _ = await client.call("tab/close", CloseTabParams(tab_id=worker.id), CloseTabResult)
        assert git(repo, "rev-parse", point.ref) == point.oid
        assert not (repo / "worker").exists()
        final = await client.call(
            "git/mergePreview",
            MergePreviewParams(project_id=project.id, source=session.branch or ""),
            MergePreviewResult,
        )
        landed = await client.call(
            "git/mergeWorktree",
            MergeWorktreeParams(
                project_id=project.id, source=final.source, target=final.target,
                source_oid=final.source_oid, target_oid=final.target_oid,
                mode="squash", confirm_token=final.confirm_token,
            ),
            GitOperationResult,
        )
        assert landed.operation.status == "finished"
        _ = await client.call(
            "workSession/close", WorkSessionParams(project_id=project.id, work_session_id=session.id),
            WorkSessionView,
        )
        removed = await client.call(
            "git/cleanupWorktree", CleanupWorktreeParams(project_id=project.id, worktree=session.worktree),
            CleanupWorktreeResult,
        )
        assert removed.kept_ref is not None
        assert removed.kept_ref.startswith(f"refs/aconsole/sessions/{session.id}/merged/")
        archived = await client.call(
            "workSession/get", WorkSessionParams(project_id=project.id, work_session_id=session.id),
            WorkSessionView,
        )
        assert archived.status == "archived"
        assert archived.workspace_removed
        assert not await asyncio.to_thread(Path(session.worktree).exists)
        assert (repo / "worker").read_text(encoding="utf-8") == "result\n"
        assert git(repo, "rev-parse", point.ref) == point.oid
        assert await client.error(
            "workSession/resume", WorkSessionParams(project_id=project.id, work_session_id=session.id),
        ) == "GIT_WORKTREE_UNKNOWN"
        await _stop(daemon, task, client)

    asyncio.run(scenario())
