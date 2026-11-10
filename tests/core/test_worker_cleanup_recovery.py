"""Worker results survive refused integration/cleanup and a daemon restart."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec
import pytest

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
from aconsole.contracts.git import GitOperationParams, GitOperationResult, MergePreviewResult
from aconsole.contracts.work_sessions import (
    WorkSessionCleanupWorkerParams,
    WorkSessionCreateParams,
    WorkSessionHistoryResult,
    WorkSessionIntegrateParams,
    WorkSessionIntegratePreviewParams,
    WorkSessionResultParams,
    WorkSessionView,
)
from aconsole.core.application.ports.git_repo import GitDone
from tests.core.git_helpers import commit, git, new_repo
from tests.core.test_wire import _Client, _connect, _daemon, _open, _stop

if TYPE_CHECKING:
    from aconsole.core.domain.git_operation import GitOperationRecord


async def _worker(
    client: _Client, repo: Path,
) -> tuple[ProjectView, WorkSessionView, TabView, WorkSessionHistoryResult]:
    project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
    session = await client.call(
        "workSession/create", WorkSessionCreateParams(project_id=project.id, title="Preserve worker"), WorkSessionView,
    )
    worker = await client.call(
        "tab/open", msgspec.structs.replace(_open(project.id), work_session_id=session.id, isolated=True), TabView,
    )
    _ = await client.call("tab/launchSpec", TabIdParams(tab_id=worker.id), TerminalLaunchSpec)
    _ = commit(Path(worker.worktree_path), "result", "valuable result\n", "worker result")
    history = await client.call(
        "workSession/result",
        WorkSessionResultParams(project_id=project.id, work_session_id=session.id, tab_id=worker.id),
        WorkSessionHistoryResult,
    )
    return project, session, worker, history


async def _accept(client: _Client, query: WorkSessionIntegratePreviewParams) -> GitOperationResult:
    preview = await client.call("workSession/integratePreview", query, MergePreviewResult)
    return await client.call("workSession/integrate", WorkSessionIntegrateParams(
        project_id=query.project_id, work_session_id=query.work_session_id, sequence=query.sequence,
        source_oid=preview.source_oid, target_oid=preview.target_oid, confirm_token=preview.confirm_token,
    ), GitOperationResult)


@pytest.mark.parametrize(
    "condition", ["dirty", "ignored", "busy", "merge_in_progress", "git_refusal", "journal_refusal"],
)
def test_refused_cleanup_preserves_worker_and_can_retry_after_restart(  # noqa: C901, PLR0915 -- refusal/restart matrix
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, condition: str,
) -> None:
    async def scenario() -> None:  # noqa: C901, PLR0912, PLR0915 -- ordered refusal, preservation and retry
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, ".gitignore", "cache\n", "seed")
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        failed_id = None
        try:
            project, session, worker, history = await _worker(client, repo)
            point = history.snapshots[-1]
            query = WorkSessionIntegratePreviewParams(
                project_id=project.id, work_session_id=session.id, sequence=point.sequence,
            )
            accepted = await _accept(client, query)
            assert accepted.operation.status == "finished"
            path = Path(worker.worktree_path)
            head = git(path, "rev-parse", "HEAD")
            target = git(Path(session.worktree), "rev-parse", "HEAD")
            cleanup = WorkSessionCleanupWorkerParams(
                project_id=project.id, work_session_id=session.id, sequence=point.sequence,
            )
            merges = daemon.git.merges
            assert merges is not None
            journal = merges._journal
            assert journal is not None
            saved_ids: set[str] = set()
            save = journal.save

            def observe(record: GitOperationRecord) -> bool:
                if condition == "journal_refusal":
                    return False
                if record.kind == "cleanup":
                    saved_ids.add(record.id)
                return save(record)

            with monkeypatch.context() as patch:
                patch.setattr(journal, "save", observe)
                if condition in {"dirty", "ignored"}:
                    saved = path / ("cache" if condition == "ignored" else "outside")
                    await asyncio.to_thread(saved.write_text, "keep me\n", encoding="utf-8")
                elif condition == "busy":
                    busy_tab = await client.call("tab/open", msgspec.structs.replace(
                        _open(project.id), workspace=WorkspaceMode.EXISTING_WORKTREE, existing_worktree=str(path),
                    ), TabView)
                elif condition == "merge_in_progress":
                    marker = Path(git(path, "rev-parse", "--git-path", "MERGE_HEAD"))
                    await asyncio.to_thread(marker.write_text, head + "\n", encoding="utf-8")
                elif condition == "git_refusal":
                    patch.setattr(merges._writer, "remove_worktree", lambda *_args, **_kw: GitDone(
                        ok=False, said="injected worktree removal refusal",
                    ))
                expected = {
                    "busy": "WORKTREE_BUSY", "merge_in_progress": "GIT_OPERATION_IN_PROGRESS",
                    "git_refusal": "INTERNAL_ERROR", "journal_refusal": "DATABASE_TRANSACTION_FAILED",
                }.get(condition, "WORKTREE_NOT_REMOVABLE")
                assert await client.error("workSession/cleanupWorker", cleanup) == expected
            assert await asyncio.to_thread(path.is_dir)
            assert (path / "result").read_text(encoding="utf-8") == "valuable result\n"
            assert git(path, "rev-parse", "HEAD") == head
            assert git(repo, "rev-parse", point.ref) == point.oid
            assert git(Path(session.worktree), "rev-parse", "HEAD") == target
            records = [journal.find(identity) for identity in saved_ids]
            if condition == "git_refusal":
                assert len(records) == 1
                assert records[0] is not None
                assert records[0].status == "failed"
                assert records[0].message == "worker worktree was not removed"
                failed_id = records[0].id
            else:
                assert records == []
            if condition in {"dirty", "ignored"}:
                saved = path / ("cache" if condition == "ignored" else "outside")
                assert saved.read_text(encoding="utf-8") == "keep me\n"
                if condition == "dirty":
                    saved.unlink()
            elif condition == "merge_in_progress":
                await asyncio.to_thread(marker.unlink)
            elif condition == "busy":
                closed = await client.call("tab/close", CloseTabParams(tab_id=busy_tab.id), CloseTabResult)
                assert closed.closed
        finally:
            await _stop(daemon, task, client)
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            if failed_id is not None:
                found = await client.call(
                    "git/operation", GitOperationParams(operation_id=failed_id), GitOperationResult,
                )
                assert found.operation.status == "failed"
            if condition == "ignored":
                assert (path / "cache").read_text(encoding="utf-8") == "keep me\n"
                assert await client.error("workSession/cleanupWorker", cleanup) == "WORKTREE_NOT_REMOVABLE"
            _ = await client.call("workSession/cleanupWorker", msgspec.structs.replace(
                cleanup, discard_ignored=condition == "ignored",
            ), EmptyResult)
            assert not await asyncio.to_thread(path.exists)
            assert git(repo, "rev-parse", point.ref) == point.oid
            assert (Path(session.worktree) / "result").read_text(encoding="utf-8") == "valuable result\n"
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


@pytest.mark.parametrize("condition", ["busy", "dirty", "staged", "merge_in_progress", "wrong_oid", "begin_snapshot"])
def test_refused_integration_preserves_both_worktrees_and_can_retry(  # noqa: C901, PLR0915 -- refusal/retry matrix
    tmp_path: Path, condition: str,
) -> None:
    async def scenario() -> None:  # noqa: C901 -- ordered refusal, preservation and retry
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "seed", "seed\n", "seed")
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            project, session, worker, history = await _worker(client, repo)
            point = history.snapshots[-1]
            query = WorkSessionIntegratePreviewParams(
                project_id=project.id, work_session_id=session.id, sequence=point.sequence,
            )
            preview = await client.call("workSession/integratePreview", query, MergePreviewResult)
            params = WorkSessionIntegrateParams(
                project_id=project.id, work_session_id=session.id, sequence=point.sequence,
                source_oid=preview.source_oid, target_oid=preview.target_oid, confirm_token=preview.confirm_token,
            )
            path = Path(session.worktree)
            head = git(path, "rev-parse", "HEAD")
            if condition in {"dirty", "staged"}:
                (path / "local").write_text("local edit\n", encoding="utf-8")
                if condition == "staged":
                    git(path, "add", "local")
            elif condition == "busy":
                coordinator = await client.call("tab/open", msgspec.structs.replace(
                    _open(project.id), work_session_id=session.id,
                ), TabView)
            elif condition == "merge_in_progress":
                marker = Path(git(path, "rev-parse", "--git-path", "MERGE_HEAD"))
                await asyncio.to_thread(marker.write_text, head + "\n", encoding="utf-8")
            elif condition == "wrong_oid":
                params = msgspec.structs.replace(params, source_oid=head)
            else:
                params = msgspec.structs.replace(params, sequence=history.snapshots[0].sequence)
                query = msgspec.structs.replace(query, sequence=params.sequence)
            expected = {
                "busy": "WORKTREE_BUSY", "dirty": "WORKTREE_DIRTY", "staged": "WORKTREE_DIRTY",
                "merge_in_progress": "GIT_OPERATION_IN_PROGRESS", "wrong_oid": "MERGE_PREVIEW_STALE",
                "begin_snapshot": "INVALID_PARAMS",
            }[condition]
            if condition != "wrong_oid":
                assert await client.error("workSession/integratePreview", query) == expected
            assert await client.error("workSession/integrate", params) == expected
            assert git(path, "rev-parse", "HEAD") == head
            assert git(repo, "rev-parse", point.ref) == point.oid
            assert (Path(worker.worktree_path) / "result").read_text(encoding="utf-8") == "valuable result\n"
            assert not (path / "result").exists()
            if condition in {"dirty", "staged"}:
                assert (path / "local").read_text(encoding="utf-8") == "local edit\n"
                if condition == "staged":
                    git(path, "reset", "--", "local")
                (path / "local").unlink()
            elif condition == "merge_in_progress":
                await asyncio.to_thread(marker.unlink)
            elif condition == "busy":
                _ = await client.call("tab/close", CloseTabParams(tab_id=coordinator.id), CloseTabResult)
            accepted = await _accept(client, msgspec.structs.replace(query, sequence=point.sequence))
            assert accepted.operation.status == "finished"
            assert (path / "result").read_text(encoding="utf-8") == "valuable result\n"
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())
