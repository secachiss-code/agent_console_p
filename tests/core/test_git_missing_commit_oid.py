"""An incomplete commit-tree result must never be acknowledged as a landed merge."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from aconsole.contracts.git import GitOperationResult
from aconsole.core.application.ports.git_repo import GitDone
from tests.core.git_helpers import git
from tests.core.test_git_merge import _diverged, _go, _preview, _start
from tests.core.test_git_operation_recovery import _operation
from tests.core.test_wire import _stop

if TYPE_CHECKING:
    from pathlib import Path

    from aconsole.contracts.git import MergeMode
    from aconsole.core.domain.git_operation import GitOperationRecord


@pytest.mark.parametrize("mode", ["merge", "squash"])
@pytest.mark.parametrize("said", ["", "incomplete commit result"])
def test_missing_commit_oid_refuses_merge_without_follow_up_and_retry_is_durable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: MergeMode,
    said: str,
) -> None:
    async def scenario() -> None:
        repo, _ = _diverged(tmp_path)
        git(repo, "branch", "release")
        before = git(repo, "rev-parse", "release")
        head = git(repo, "rev-parse", "HEAD")
        daemon, task, client, project = await _start(tmp_path, repo)
        try:
            merges = daemon.git.merges
            assert merges is not None
            followed: list[str] = []

            def incomplete(*_args: object, **_kwargs: object) -> GitDone:
                return GitDone(ok=True, said=said, oid=None)

            def follow(record: GitOperationRecord) -> None:
                followed.append(record.id)

            preview = await _preview(client, project, "work", "release")
            with monkeypatch.context() as patch:
                patch.setattr(merges._writer, "commit_tree", incomplete)
                patch.setattr(merges, "follow_up", follow)
                result = await client.call("git/mergeWorktree", _go(project, preview, mode), GitOperationResult)
            assert result.operation.status == "failed"
            assert result.operation.result_oid is None
            assert result.operation.follow_up_pending is False
            assert result.operation.message == (said or "git commit-tree returned no commit id")
            assert followed == []
            assert git(repo, "rev-parse", "release") == before
            assert git(repo, "rev-parse", "HEAD") == head
            assert git(repo, "status", "--porcelain") == ""
            failed_id = result.operation.id
            preview = await _preview(client, project, "work", "release")
            retry = await client.call("git/mergeWorktree", _go(project, preview, mode), GitOperationResult)
            assert retry.operation.status == "finished"
            assert retry.operation.result_oid == git(repo, "rev-parse", "release")
            success_id = retry.operation.id
            tip = retry.operation.result_oid
        finally:
            await _stop(daemon, task, client)
        daemon, task, client, _ = await _start(tmp_path, repo)
        try:
            assert (await _operation(client, failed_id)).status == "failed"
            successful = await _operation(client, success_id)
            assert successful.status == "finished"
            assert successful.result_oid == tip
            assert git(repo, "rev-parse", "HEAD") == head
            assert git(repo, "rev-parse", "release") == tip
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())
