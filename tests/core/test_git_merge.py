"""Merges through the real daemon, in real repositories, with and without a worktree on the target."""

from __future__ import annotations

import asyncio
import dataclasses
import shlex
from datetime import UTC, datetime
lazy from pathlib import Path

import pytest

from aconsole.contracts.app import ProjectAddParams, ProjectView
from aconsole.contracts.git import (
    GitOperationParams,
    GitOperationResult,
    GitStateParams,
    GitStateResult,
    MergeMode,
    MergePreviewParams,
    MergePreviewResult,
    MergeWorktreeParams,
)
from aconsole.core.domain.git_operation import GitOperationRecord, landed, merge_modes
from tests.core.git_helpers import add_worktree, commit, git, new_repo
from tests.core.test_wire import _Client, _connect, _daemon, _stop
lazy from aconsole.contracts.common import ProjectId


def _diverged(tmp_path: Path) -> tuple[Path, Path]:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "a.txt", "1\n", "first")
    tree = add_worktree(repo, tmp_path / "tree", "work")
    _ = commit(tree, "w.txt", "w\n", "from work")
    _ = commit(repo, "b.txt", "b\n", "on base")
    return repo, tree


async def _preview(client: _Client, project: ProjectId, source: str, target: str | None = None) -> MergePreviewResult:
    params = MergePreviewParams(project_id=project, source=source, target=target)
    return await client.call("git/mergePreview", params, MergePreviewResult)


def _go(project: ProjectId, preview: MergePreviewResult, mode: MergeMode) -> MergeWorktreeParams:
    return MergeWorktreeParams(
        project_id=project,
        source=preview.source,
        target=preview.target,
        source_oid=preview.source_oid,
        target_oid=preview.target_oid,
        mode=mode,
        confirm_token=preview.confirm_token,
    )


async def _start(tmp_path: Path, repo: Path) -> tuple[object, asyncio.Task[int], _Client, ProjectId]:
    daemon = _daemon(tmp_path, serve_ingest=False)
    task = asyncio.create_task(daemon.serve())
    client = await _connect(daemon.socket)
    project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
    return daemon, task, client, project.id


def test_each_mode_through_the_target_worktree(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo, _tree = _diverged(tmp_path)
        base = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        daemon, task, client, project = await _start(tmp_path, repo)
        preview = await _preview(client, project, "work", base)
        assert preview.modes == ("merge", "squash")
        assert preview.blockers == ()
        assert preview.target_worktree is not None

        result = await client.call("git/mergeWorktree", _go(project, preview, "merge"), GitOperationResult)
        operation = result.operation
        assert operation.status == "finished", operation.message
        assert operation.result_oid == git(repo, "rev-parse", "HEAD")
        assert git(repo, "log", "-1", "--format=%P") == f"{preview.target_oid} {preview.source_oid}"
        assert (repo / "w.txt").exists()

        again = await client.call("git/operation", GitOperationParams(operation_id=operation.id), GitOperationResult)
        assert again.operation.status == "finished"

        # The same branch is now merged. Nothing further can be done with it.
        after = await _preview(client, project, "work", base)
        assert after.modes == ()
        assert after.blockers == ("already_merged",)
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_fast_forward_and_squash(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a.txt", "1\n", "first")
        base = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        tree = add_worktree(repo, tmp_path / "ff", "ff")
        tip = commit(tree, "f.txt", "f\n", "ff work")
        daemon, task, client, project = await _start(tmp_path, repo)
        preview = await _preview(client, project, "ff", base)
        assert preview.modes == ("fast_forward", "merge", "squash")
        done = await client.call("git/mergeWorktree", _go(project, preview, "fast_forward"), GitOperationResult)
        assert done.operation.status == "finished"
        assert git(repo, "rev-parse", "HEAD") == tip

        other = add_worktree(repo, tmp_path / "sq", "sq")
        _ = commit(other, "s.txt", "1\n", "s1")
        _ = commit(other, "s.txt", "2\n", "s2")
        preview = await _preview(client, project, "sq", base)
        params = _go(project, preview, "squash")
        made = await client.call("git/mergeWorktree", params, GitOperationResult)
        assert made.operation.status == "finished"
        assert git(repo, "log", "-1", "--format=%P") == preview.target_oid
        assert git(repo, "log", "-1", "--format=%s") == f"Squash sq into {base}"
        assert (repo / "s.txt").read_text(encoding="utf-8") == "2\n"
        assert git(repo, "status", "--porcelain") == ""
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_a_target_with_no_worktree_moves_by_its_ref(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo, _tree = _diverged(tmp_path)
        start = git(repo, "rev-parse", "HEAD~1")
        _ = git(repo, "branch", "release", start)
        before = git(repo, "status", "--porcelain")
        daemon, task, client, project = await _start(tmp_path, repo)
        preview = await _preview(client, project, "work", "release")
        assert preview.target_worktree is None
        assert preview.modes[0] == "fast_forward"
        done = await client.call("git/mergeWorktree", _go(project, preview, "fast_forward"), GitOperationResult)
        assert done.operation.status == "finished"
        assert git(repo, "rev-parse", "release") == preview.source_oid

        _ = git(repo, "branch", "two", start)
        _ = commit(repo, "z.txt", "z\n", "more on base")
        base = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        _ = git(repo, "branch", "-f", "two", base)
        preview = await _preview(client, project, "work", "two")
        done = await client.call("git/mergeWorktree", _go(project, preview, "merge"), GitOperationResult)
        assert done.operation.status == "finished"
        assert git(repo, "log", "-1", "--format=%P", "two") == f"{preview.target_oid} {preview.source_oid}"
        assert git(repo, "status", "--porcelain") == before
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_a_branch_that_moved_after_the_preview_is_refused(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo, tree = _diverged(tmp_path)
        base = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        daemon, task, client, project = await _start(tmp_path, repo)
        preview = await _preview(client, project, "work", base)
        _ = commit(tree, "w2.txt", "2\n", "newer than the preview")
        head = git(repo, "rev-parse", "HEAD")
        name = await client.error("git/mergeWorktree", _go(project, preview, "merge"))
        assert name == "MERGE_PREVIEW_STALE"
        assert git(repo, "rev-parse", "HEAD") == head

        preview = await _preview(client, project, "work", base)
        used = _go(project, preview, "merge")
        _ = await client.call("git/mergeWorktree", used, GitOperationResult)
        assert await client.error("git/mergeWorktree", used) == "CONFIRM_TOKEN_INVALID"
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_conflict_and_dirty_target_block_while_a_dirty_source_does_not(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a.txt", "1\n", "first")
        base = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        tree = add_worktree(repo, tmp_path / "tree", "work")
        _ = commit(tree, "a.txt", "work\n", "work side")
        _ = commit(repo, "a.txt", "base\n", "base side")
        daemon, task, client, project = await _start(tmp_path, repo)
        preview = await _preview(client, project, "work", base)
        assert preview.probe.status == "conflict"
        assert preview.probe.conflicts == ("a.txt",)
        assert "conflict" in preview.blockers
        assert await client.error("git/mergeWorktree", _go(project, preview, "merge")) == "MERGE_CONFLICT"

        clean = add_worktree(repo, tmp_path / "clean", "clean")
        _ = commit(clean, "c.txt", "c\n", "clean work")
        (clean / "wip.txt").write_text("not committed\n", encoding="utf-8")
        preview = await _preview(client, project, "clean", base)
        assert preview.source_dirty
        assert preview.blockers == ()

        (repo / "a.txt").write_text("edit\n", encoding="utf-8")
        preview = await _preview(client, project, "clean", base)
        assert preview.blockers == ("target_dirty",)
        assert await client.error("git/mergeWorktree", _go(project, preview, "merge")) == "MERGE_MODE_UNAVAILABLE"
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_a_refusing_hook_leaves_the_target_as_it_was(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo, _tree = _diverged(tmp_path)
        base = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        head = git(repo, "rev-parse", "HEAD")
        for name in ("pre-merge-commit", "pre-commit"):
            hook = repo / ".git" / "hooks" / name
            hook.write_text("#!/bin/sh\necho 'policy says no' >&2\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)
        daemon, task, client, project = await _start(tmp_path, repo)
        for mode in ("merge", "squash"):
            preview = await _preview(client, project, "work", base)
            result = await client.call("git/mergeWorktree", _go(project, preview, mode), GitOperationResult)
            assert result.operation.status == "failed"
            assert "policy says no" in (result.operation.message or "")
            assert git(repo, "rev-parse", "HEAD") == head
            assert git(repo, "status", "--porcelain") == ""
            state = await client.call("git/state", GitStateParams(project_id=project), GitStateResult)
            assert state.operations == ()
        await _stop(daemon, task, client)

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["pre-commit", "commit-msg"])
@pytest.mark.parametrize("change", ["add", "remove"])
def test_no_checkout_squash_refuses_a_hook_changed_index(tmp_path: Path, phase: str, change: str) -> None:
    async def scenario() -> None:
        repo, source = _diverged(tmp_path)
        target = git(repo, "rev-parse", "HEAD")
        _ = git(repo, "branch", "release", target)
        (repo / "local.txt").write_text("staged local work\n", encoding="utf-8")
        _ = git(repo, "add", "--", "local.txt")
        (repo / "a.txt").write_text("unstaged local work\n", encoding="utf-8")
        (repo / ".git" / "info" / "exclude").write_text("ignored.txt\n", encoding="utf-8")
        (repo / "ignored.txt").write_text("ignored local work\n", encoding="utf-8")
        (repo / "untracked.txt").write_text("untracked local work\n", encoding="utf-8")
        original_index = (repo / ".git" / "index").read_bytes()
        source_oid = git(source, "rev-parse", "HEAD")
        called = tmp_path / "called"
        mutation = (
            "printf 'hook work\\n' > hook-added.txt\n/usr/bin/git add -- hook-added.txt\n"
            if change == "add" else "/usr/bin/git update-index --force-remove -- a.txt\n"
        )
        hook = repo / ".git" / "hooks" / phase
        hook.write_text(f"#!/bin/sh\nset -e\nprintf called >> {shlex.quote(str(called))}\n{mutation}",
                        encoding="utf-8")
        hook.chmod(0o755)
        daemon, task, client, project = await _start(tmp_path, repo)
        try:
            preview = await _preview(client, project, "work", "release")
            assert preview.target_worktree is None
            done = await client.call("git/mergeWorktree", _go(project, preview, "squash"), GitOperationResult)
            assert done.operation.status == "failed", done.operation
            assert "hook changed the candidate index" in (done.operation.message or "")
            assert done.operation.result_oid is None
            assert git(repo, "rev-parse", "release") == target
            assert git(repo, "rev-parse", "HEAD") == target
            assert git(source, "rev-parse", "HEAD") == source_oid
            assert (repo / ".git" / "index").read_bytes() == original_index
            assert (repo / "a.txt").read_text(encoding="utf-8") == "unstaged local work\n"
            assert (repo / "local.txt").read_text(encoding="utf-8") == "staged local work\n"
            assert (repo / "ignored.txt").read_text(encoding="utf-8") == "ignored local work\n"
            assert (repo / "untracked.txt").read_text(encoding="utf-8") == "untracked local work\n"
            assert called.read_text(encoding="utf-8") == "called"
            if change == "add":
                assert (repo / "hook-added.txt").read_text(encoding="utf-8") == "hook work\n"
            hook.unlink()
            retry = await _preview(client, project, "work", "release")
            retried = await client.call("git/mergeWorktree", _go(project, retry, "squash"), GitOperationResult)
            assert retried.operation.status == "finished", retried.operation
            assert git(repo, "ls-tree", "--name-only", "release") == "a.txt\nb.txt\nw.txt"
            assert git(repo, "rev-parse", "HEAD") == target
            assert (repo / ".git" / "index").read_bytes() == original_index
            assert called.read_text(encoding="utf-8") == "called"
            previous = await client.call(
                "git/operation", GitOperationParams(operation_id=done.operation.id), GitOperationResult,
            )
            assert previous.operation.status == "failed"
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_no_checkout_squash_keeps_a_hook_edited_message(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo, _source = _diverged(tmp_path)
        _ = git(repo, "branch", "release", "HEAD")
        hook = repo / ".git" / "hooks" / "commit-msg"
        hook.write_text('#!/bin/sh\nprintf "\\nHook-approved message\\n" >> "$1"\n', encoding="utf-8")
        hook.chmod(0o755)
        daemon, task, client, project = await _start(tmp_path, repo)
        try:
            preview = await _preview(client, project, "work", "release")
            assert preview.target_worktree is None
            done = await client.call("git/mergeWorktree", _go(project, preview, "squash"), GitOperationResult)
            assert done.operation.status == "finished", done.operation
            assert "Hook-approved message" in git(repo, "log", "-1", "--format=%B", "release")
            assert git(repo, "ls-tree", "--name-only", "release") == "a.txt\nb.txt\nw.txt"
            assert git(repo, "status", "--porcelain") == ""
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_a_restart_settles_a_merge_from_the_branch_itself(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo, _tree = _diverged(tmp_path)
        base = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        daemon, task, client, project = await _start(tmp_path, repo)
        preview = await _preview(client, project, "work", base)
        merges = daemon.git.merges
        assert merges is not None
        journal = merges._journal
        assert journal is not None
        at = datetime.now(UTC)
        landed_id = "a" * 32
        lost_id = "b" * 32
        common = {
            "project_id": str(project),
            "kind": "merge",
            "status": "started",
            "source": "work",
            "target": base,
            "source_oid": preview.source_oid,
            "target_oid": preview.target_oid,
            "result_oid": None,
            "expected_tree": None,
            "worktree": str(repo),
            "tab_id": None,
            "plan_node": None,
            "message": None,
            "follow_up_pending": False,
            "created_at": at,
            "updated_at": at,
        }
        assert journal.save(GitOperationRecord(id=lost_id, mode="merge", **common))
        await _stop(daemon, task, client)

        # The first restart finds the branch where it was: the merge never happened.
        daemon, task, client, _project = await _reopen(tmp_path, repo)
        lost = await client.call("git/operation", GitOperationParams(operation_id=lost_id), GitOperationResult)
        assert lost.operation.status == "failed"
        merges = daemon.git.merges
        assert merges is not None
        journal = merges._journal
        assert journal is not None
        assert journal.save(GitOperationRecord(id=landed_id, mode="merge", **common))
        await _stop(daemon, task, client)

        # A crash after Git finished but before the journal heard about it.
        _ = git(repo, "merge", "--no-ff", "-m", "by hand", preview.source_oid)
        daemon, task, client, _project = await _reopen(tmp_path, repo)
        found = await client.call("git/operation", GitOperationParams(operation_id=landed_id), GitOperationResult)
        assert found.operation.status == "finished"
        assert found.operation.result_oid == git(repo, "rev-parse", "HEAD")
        await _stop(daemon, task, client)

    asyncio.run(scenario())


async def _reopen(tmp_path: Path, repo: Path) -> tuple[object, asyncio.Task[int], _Client, ProjectId]:
    return await _start(tmp_path, repo)


def test_domain_rules() -> None:
    assert merge_modes(clean=True, already_merged=True, can_fast_forward=True) == ()
    assert merge_modes(clean=False, already_merged=False, can_fast_forward=False) == ()
    at = datetime.now(UTC)
    record = GitOperationRecord(
        id="x",
        project_id="p",
        kind="merge",
        status="started",
        mode="squash",
        source="s",
        target="t",
        source_oid="so",
        target_oid="to",
        result_oid=None,
        expected_tree="tree",
        worktree=None,
        tab_id=None,
        plan_node=None,
        message=None,
        follow_up_pending=False,
        created_at=at,
        updated_at=at,
    )
    assert landed(record, head="h", parents=("to",), tree="tree")
    assert not landed(record, head="h", parents=("to",), tree="other")
    assert not landed(dataclasses.replace(record, mode="merge"), head="h", parents=("to",), tree="tree")
    assert landed(dataclasses.replace(record, mode="fast_forward"), head="so", parents=(), tree=None)
