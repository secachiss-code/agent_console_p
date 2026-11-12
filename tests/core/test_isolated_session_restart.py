"""Restoring isolated tabs must preserve the identity of the primary workspace."""

from __future__ import annotations

import asyncio
from pathlib import Path

import msgspec
import pytest

from aconsole.contracts.app import EmptyResult, ProjectAddParams, ProjectView, TabView
from aconsole.contracts.git import GitOperationResult, MergePreviewResult
from aconsole.contracts.work_sessions import (
    WorkSessionCleanupWorkerParams,
    WorkSessionCreateParams,
    WorkSessionHistoryResult,
    WorkSessionIntegrateParams,
    WorkSessionIntegratePreviewParams,
    WorkSessionParams,
    WorkSessionResultParams,
    WorkSessionView,
)
from tests.core.git_helpers import commit, git, new_repo
from tests.core.test_wire import _connect, _daemon, _open, _stop


@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("coordinator", [False, True])
def test_isolated_tabs_do_not_replace_the_primary_session_after_restart(
    tmp_path: Path, *, archived: bool, coordinator: bool,
) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "seed", "seed\n", "seed")
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            session = await client.call("workSession/create", WorkSessionCreateParams(
                project_id=project.id, title="Primary task",
            ), WorkSessionView)
            worker = await client.call("tab/open", msgspec.structs.replace(
                _open(project.id), work_session_id=session.id, isolated=True,
            ), TabView)
            path = Path(worker.worktree_path)
            _ = commit(path, "result", "worker result\n", "result")
            history = await client.call("workSession/result", WorkSessionResultParams(
                project_id=project.id, work_session_id=session.id, tab_id=worker.id,
            ), WorkSessionHistoryResult)
            point = history.snapshots[-1]
            preview = await client.call("workSession/integratePreview", WorkSessionIntegratePreviewParams(
                project_id=project.id, work_session_id=session.id, sequence=point.sequence,
            ), MergePreviewResult)
            result = await client.call("workSession/integrate", WorkSessionIntegrateParams(
                project_id=project.id, work_session_id=session.id, sequence=point.sequence,
                source_oid=preview.source_oid, target_oid=preview.target_oid, confirm_token=preview.confirm_token,
            ), GitOperationResult)
            assert result.operation.status == "finished"
            if coordinator:
                _ = await client.call("tab/open", msgspec.structs.replace(
                    _open(project.id), work_session_id=session.id,
                ), TabView)
            query = WorkSessionParams(project_id=project.id, work_session_id=session.id)
            if archived:
                session = await client.call("workSession/close", query, WorkSessionView)
            before = git(repo, "show-ref")
        finally:
            await _stop(daemon, task, client)
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            restored = await client.call("workSession/get", query, WorkSessionView)
            assert restored == session
            assert git(repo, "show-ref") == before
            snapshot = await client.snapshot()
            restored_worker = next(tab for tab in snapshot.tabs if tab.id == worker.id)
            assert restored_worker.isolated
            assert restored_worker.work_session_id == session.id
            assert restored_worker.worktree_path == str(path)
            assert path != Path(restored.worktree)
            assert (Path(restored.worktree) / "result").read_text(encoding="utf-8") == "worker result\n"
            _ = await client.call("workSession/cleanupWorker", WorkSessionCleanupWorkerParams(
                project_id=project.id, work_session_id=session.id, sequence=point.sequence,
            ), EmptyResult)
            assert not await asyncio.to_thread(path.exists)
            assert git(repo, "rev-parse", point.ref) == point.oid
            assert await client.call("workSession/get", query, WorkSessionView) == session
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())
