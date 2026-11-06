"""A WorkSession result is the recorded commit, even when its public Git ref moves."""

from __future__ import annotations

import asyncio
from pathlib import Path

import msgspec
import pytest

from aconsole.contracts.app import ProjectAddParams, ProjectView, TabView
from aconsole.contracts.git import GitOperationResult, MergePreviewParams, MergePreviewResult, MergeWorktreeParams
from aconsole.contracts.work_sessions import (
    WorkSessionCleanupWorkerParams,
    WorkSessionCreateParams,
    WorkSessionHistoryResult,
    WorkSessionIntegrateParams,
    WorkSessionIntegratePreviewParams,
    WorkSessionResultParams,
    WorkSessionView,
)
from tests.core.git_helpers import commit, git, new_repo
from tests.core.test_wire import _connect, _daemon, _open, _stop


@pytest.mark.parametrize("entry", ["session_preview", "git_preview", "session_integrate", "git_integrate"])
def test_a_result_ref_cannot_replace_the_recorded_worker_result(tmp_path: Path, entry: str) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "base", "base\n", "seed")
        daemon = _daemon(tmp_path, serve_ingest=False)
        serving = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            session = await client.call(
                "workSession/create", WorkSessionCreateParams(project_id=project.id, title="Pinned result"),
                WorkSessionView,
            )
            worker = await client.call(
                "tab/open",
                msgspec.structs.replace(_open(project.id), work_session_id=session.id, isolated=True), TabView,
            )
            path = Path(worker.worktree_path)
            _ = commit(path, "expected", "reviewed result\n", "worker result")
            history = await client.call(
                "workSession/result", WorkSessionResultParams(
                    project_id=project.id, work_session_id=session.id, tab_id=worker.id,
                ), WorkSessionHistoryResult,
            )
            point = history.snapshots[-1]
            query = WorkSessionIntegratePreviewParams(
                project_id=project.id, work_session_id=session.id, sequence=point.sequence,
            )
            before = git(Path(session.worktree), "rev-parse", "HEAD")
            preview = await client.call("workSession/integratePreview", query, MergePreviewResult)
            replacement = commit(path, "unreviewed", "must stay in worker\n", "later worker change")
            _ = git(repo, "update-ref", point.ref, replacement)
            if entry == "session_preview":
                error = await client.error("workSession/integratePreview", query)
            elif entry == "git_preview":
                error = await client.error(
                    "git/mergePreview", MergePreviewParams(
                        project_id=project.id, source=point.ref, target=session.branch,
                    ),
                )
            elif entry == "session_integrate":
                error = await client.error("workSession/integrate", WorkSessionIntegrateParams(
                    project_id=project.id, work_session_id=session.id, sequence=point.sequence,
                    source_oid=preview.source_oid, target_oid=preview.target_oid, confirm_token=preview.confirm_token,
                ))
            else:
                error = await client.error("git/mergeWorktree", MergeWorktreeParams(
                    project_id=project.id, source=point.ref, target=preview.target, mode="merge",
                    source_oid=preview.source_oid, target_oid=preview.target_oid, confirm_token=preview.confirm_token,
                ))
            assert error == "MERGE_PREVIEW_STALE"
            assert git(Path(session.worktree), "rev-parse", "HEAD") == before
            assert not (Path(session.worktree) / "unreviewed").exists()
            assert (path / "unreviewed").read_text(encoding="utf-8") == "must stay in worker\n"
            _ = git(repo, "update-ref", point.ref, point.oid or "")
            fresh = await client.call("workSession/integratePreview", query, MergePreviewResult)
            accepted = await client.call("workSession/integrate", WorkSessionIntegrateParams(
                project_id=project.id, work_session_id=session.id, sequence=point.sequence,
                source_oid=fresh.source_oid, target_oid=fresh.target_oid, confirm_token=fresh.confirm_token,
            ), GitOperationResult)
            assert accepted.operation.source_oid == point.oid
            assert (Path(session.worktree) / "expected").read_text(encoding="utf-8") == "reviewed result\n"
            assert not (Path(session.worktree) / "unreviewed").exists()
            assert await client.error("workSession/cleanupWorker", WorkSessionCleanupWorkerParams(
                project_id=project.id, work_session_id=session.id, sequence=point.sequence,
            )) == "WORKTREE_NOT_REMOVABLE"
            assert await asyncio.to_thread(path.exists)
            assert (path / "unreviewed").exists()
        finally:
            await _stop(daemon, serving, client)

    asyncio.run(scenario())
