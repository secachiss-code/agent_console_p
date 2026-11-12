"""Real Git and SQLite: refusal before mutation, retry, and crash reconciliation."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from aconsole.contracts.git import GitOperationParams, GitOperationResult
from aconsole.core.application.ports.git_repo import GitDone
from aconsole.core.domain.git_operation import INTERRUPTED, TARGET_MOVED, GitOperationRecord
from tests.core.git_helpers import commit, git
from tests.core.test_git_merge import _diverged, _go, _preview, _start
from tests.core.test_wire import _Client, _stop

if TYPE_CHECKING:
    from pathlib import Path

    from aconsole.contracts.git import GitOperationView, MergePreviewResult


async def _operation(client: _Client, identity: str) -> GitOperationView:
    return (await client.call("git/operation", GitOperationParams(operation_id=identity), GitOperationResult)).operation


def _record(project: str, preview: MergePreviewResult, worktree: str | None) -> GitOperationRecord:
    now = datetime.now(UTC)
    return GitOperationRecord(
        id="a" * 32,
        project_id=project,
        kind="merge",
        status="started",
        mode="merge",
        source=preview.source,
        target=preview.target,
        source_oid=preview.source_oid,
        target_oid=preview.target_oid,
        result_oid=None,
        expected_tree=None,
        worktree=worktree,
        tab_id=None,
        plan_node=None,
        message=None,
        follow_up_pending=False,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize("target_has_worktree", [False, True])
def test_journal_refusal_prevents_merge_and_a_fresh_preview_can_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    target_has_worktree: bool,
) -> None:
    async def scenario() -> None:
        repo, _ = _diverged(tmp_path)
        target = git(repo, "branch", "--show-current") if target_has_worktree else "release"
        if not target_has_worktree:
            git(repo, "branch", target)
        daemon, task, client, project = await _start(tmp_path, repo)
        try:
            merges = daemon.git.merges
            assert merges is not None
            journal = merges._journal
            assert journal is not None
            before = git(repo, "rev-parse", target)
            preview = await _preview(client, project, "work", target)
            with monkeypatch.context() as patch:
                patch.setattr(journal, "save", lambda _record: False)
                assert (
                    await client.error("git/mergeWorktree", _go(project, preview, "merge"))
                    == "DATABASE_TRANSACTION_FAILED"
                )
            assert git(repo, "rev-parse", target) == before
            assert git(repo, "status", "--porcelain") == ""
            assert journal.unfinished() == ()
            assert merges.of_project(str(project)) == ()
            preview = await _preview(client, project, "work", target)
            result = await client.call("git/mergeWorktree", _go(project, preview, "merge"), GitOperationResult)
            assert result.operation.status == "finished"
            assert result.operation.result_oid == git(repo, "rev-parse", target)
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["merge_hooks", "commit_tree", "move_ref"])
def test_unchecked_target_ref_stays_unchanged_when_git_refuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    async def scenario() -> None:
        repo, _ = _diverged(tmp_path)
        git(repo, "branch", "release")
        daemon, task, client, project = await _start(tmp_path, repo)
        try:
            merges = daemon.git.merges
            assert merges is not None
            preview = await _preview(client, project, "work", "release")
            head = git(repo, "rev-parse", "HEAD")

            def refuse(*_args: object, **_kwargs: object) -> GitDone:
                return GitDone(ok=False, said="injected Git refusal")

            with monkeypatch.context() as patch:
                patch.setattr(merges._writer, failure, refuse)
                result = await client.call("git/mergeWorktree", _go(project, preview, "merge"), GitOperationResult)
            assert result.operation.status == "failed"
            assert result.operation.message == "injected Git refusal"
            assert git(repo, "rev-parse", "release") == preview.target_oid
            assert git(repo, "rev-parse", "HEAD") == head
            assert git(repo, "status", "--porcelain") == ""
            found = await _operation(client, result.operation.id)
            assert found.status == "failed"
            assert merges.waiting() is False
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["follow_up", "journal_after_begin"])
def test_a_landed_merge_is_kept_when_bookkeeping_fails_and_retry_finishes_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    async def scenario() -> None:
        repo, _ = _diverged(tmp_path)
        target = git(repo, "branch", "--show-current")
        daemon, task, client, project = await _start(tmp_path, repo)
        try:
            merges = daemon.git.merges
            assert merges is not None
            journal = merges._journal
            assert journal is not None
            preview = await _preview(client, project, "work", target)
            save = journal.save

            def fail_after_begin(record: GitOperationRecord) -> bool:
                return save(record) if record.status == "started" else False

            def fail_follow(_record: GitOperationRecord) -> None:
                message = "vault unavailable"
                raise OSError(message)

            with monkeypatch.context() as patch:
                if failure == "follow_up":
                    patch.setattr(merges, "follow_up", fail_follow)
                else:
                    patch.setattr(journal, "save", fail_after_begin)
                result = await client.call("git/mergeWorktree", _go(project, preview, "merge"), GitOperationResult)
                assert result.operation.status == ("merged" if failure == "follow_up" else "finished")
                assert result.operation.follow_up_pending is (failure == "follow_up")
                assert merges.waiting() is True
                durable = journal.find(result.operation.id)
                assert durable is not None
                assert durable.status == ("merged" if failure == "follow_up" else "started")
                tip = git(repo, "rev-parse", target)
                assert tip == result.operation.result_oid
                assert git(repo, "log", "-1", "--format=%P") == f"{preview.target_oid} {preview.source_oid}"
            await daemon._in_worker(merges.retry)
            assert merges.waiting() is False
            assert (await _operation(client, result.operation.id)).status == "finished"
            assert git(repo, "rev-parse", target) == tip
            identity = result.operation.id
        finally:
            await _stop(daemon, task, client)
        daemon, task, client, _ = await _start(tmp_path, repo)
        try:
            found = await _operation(client, identity)
            assert found.status == "finished"
            assert found.result_oid == tip
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "condition", ["cleanup_present", "cleanup_missing", "cleanup_no_path", "target_deleted", "target_moved"]
)
def test_restart_reconciles_interrupted_operations_without_mutating_git(tmp_path: Path, condition: str) -> None:
    async def scenario() -> None:
        repo, _ = _diverged(tmp_path)
        git(repo, "branch", "release")
        daemon, task, client, project = await _start(tmp_path, repo)
        try:
            preview = await _preview(client, project, "work", "release")
            record = _record(str(project), preview, str(repo))
            if condition.startswith("cleanup"):
                path = str(repo) if condition == "cleanup_present" else str(tmp_path / "gone")
                record = replace(
                    record, kind="cleanup", mode=None, worktree=None if condition == "cleanup_no_path" else path
                )
            merges = daemon.git.merges
            assert merges is not None
            assert merges._journal is not None
            assert merges._journal.save(record)
        finally:
            await _stop(daemon, task, client)
        if condition == "target_deleted":
            git(repo, "branch", "-D", "release")
        elif condition == "target_moved":
            tip = commit(repo, "later.txt", "later\n", "independent commit")
            git(repo, "branch", "-f", "release", tip)
        before = git(repo, "show-ref")
        daemon, task, client, _ = await _start(tmp_path, repo)
        try:
            found = await _operation(client, record.id)
            assert found.status == ("finished" if condition in {"cleanup_missing", "cleanup_no_path"} else "failed")
            assert found.result_oid is None
            if condition == "target_moved":
                assert found.message == TARGET_MOVED
            elif condition == "target_deleted":
                assert "GIT_BRANCH_UNKNOWN" in (found.message or "")
            else:
                assert found.message == INTERRUPTED
            assert git(repo, "show-ref") == before
            assert git(repo, "status", "--porcelain") == ""
            assert (repo / "b.txt").read_text(encoding="utf-8") == "b\n"
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())
