"""A failed journal stops the work, commits say whose they are, and a merge with no worktree still asks the hooks."""

from __future__ import annotations

import asyncio
from pathlib import Path

import msgspec
import pytest

from aconsole.contracts.app import ProjectAddParams, ProjectView, TabView
from aconsole.contracts.common import ProjectId, WorkspaceMode
from aconsole.contracts.git import (
    CleanupWorktreeParams,
    GitChangeResult,
    GitCommitParams,
    GitGraphParams,
    GitGraphResult,
    GitOperationResult,
    GitStateParams,
    GitStateResult,
    MergeMode,
    MergePreviewParams,
    MergePreviewResult,
    MergeWorktreeParams,
    RebaseParams,
)
from aconsole.core.infrastructure.mcp.tools import NodeIn, TabContext
from tests.core.git_helpers import add_worktree, commit, git, new_repo
from tests.core.test_wire import _Client, _connect, _daemon, _open, _stop


async def _start(tmp_path: Path, repo: Path) -> tuple[object, asyncio.Task[int], _Client, ProjectId]:
    daemon = _daemon(tmp_path, serve_ingest=False)
    task = asyncio.create_task(daemon.serve())
    client = await _connect(daemon.socket)
    project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
    return daemon, task, client, project.id


async def _merge_params(
    client: _Client, project: ProjectId, source: str, target: str, mode: MergeMode
) -> MergeWorktreeParams:
    preview = await client.call(
        "git/mergePreview", MergePreviewParams(project_id=project, source=source, target=target), MergePreviewResult
    )
    return MergeWorktreeParams(
        project_id=project,
        source=preview.source,
        target=preview.target,
        source_oid=preview.source_oid,
        target_oid=preview.target_oid,
        mode=mode,
        confirm_token=preview.confirm_token,
    )


def _hook(repo: Path, name: str, body: str) -> None:
    path = repo / ".git" / "hooks" / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def test_a_journal_that_cannot_write_stops_every_operation(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a.txt", "1\n", "first")
        base = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        tree = add_worktree(repo, tmp_path / "tree", "work")
        _ = commit(tree, "w.txt", "w\n", "work")
        onto = commit(repo, "b.txt", "b\n", "base")
        daemon, task, client, project = await _start(tmp_path, repo)
        params = await _merge_params(client, project, "work", base, "merge")
        merges = daemon.git.merges
        assert merges is not None
        journal = merges._journal
        assert journal is not None
        journal.save = lambda _record: False

        heads = (git(repo, "rev-parse", "HEAD"), git(tree, "rev-parse", "HEAD"))
        assert await client.error("git/mergeWorktree", params) == "DATABASE_TRANSACTION_FAILED"
        rebase = RebaseParams(project_id=project, worktree=str(tree), onto_oid=onto, expect_head=heads[1])
        assert await client.error("git/rebase", rebase) == "DATABASE_TRANSACTION_FAILED"
        assert (git(repo, "rev-parse", "HEAD"), git(tree, "rev-parse", "HEAD")) == heads
        assert git(tree, "for-each-ref", "refs/aconsole/before") == ""

        state = await client.call("git/state", GitStateParams(project_id=project), GitStateResult)
        assert state.operations == ()
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_cleanup_is_journalled_and_stopped_the_same_way(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a.txt", "1\n", "first")
        tree = add_worktree(repo, tmp_path / "tree", "gone")
        daemon, task, client, project = await _start(tmp_path, repo)
        merges = daemon.git.merges
        assert merges is not None
        journal = merges._journal
        assert journal is not None
        real = journal.save
        journal.save = lambda _record: False
        ask = CleanupWorktreeParams(project_id=project, worktree=str(tree))
        assert await client.error("git/cleanupWorktree", ask) == "DATABASE_TRANSACTION_FAILED"
        assert tree.exists()
        journal.save = real
        await _stop(daemon, task, client)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["merge", "squash"])
def test_commits_keep_their_node_after_the_tab_and_branch_are_gone(tmp_path: Path, mode: MergeMode) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a.txt", "1\n", "first")
        base = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        daemon, task, client, project = await _start(tmp_path, repo)
        tab = await client.call(
            "tab/open", msgspec.structs.replace(_open(project), workspace=WorkspaceMode.NEW_WORKTREE), TabView
        )
        tree = Path(tab.worktree_path)
        ctx = TabContext(tab_id=str(tab.id), project_id=str(project), worktree=tree, agent="claude")
        board = daemon._plans
        created = board.create(ctx, "plan", "goal", [NodeIn(key="AB-1", title="node one", objective="do it")])
        node = created.nodes[0].node_id
        _ = board.claim(ctx, node)

        (tree / "n.txt").write_text("n\n", encoding="utf-8")
        _ = git(tree, "add", "n.txt")
        state = await client.call("git/state", GitStateParams(project_id=project), GitStateResult)
        row = next(item for item in state.worktrees if item.tab_id == tab.id)
        made = await client.call(
            "git/commit",
            GitCommitParams(
                project_id=project,
                worktree=row.path,
                message="made here\n\nCo-Authored-By: someone <s@e>",
                expect_head=row.head_oid,
                expect_index=row.index_digest,
            ),
            GitChangeResult,
        )
        assert made.ok, made.detail
        body = git(tree, "log", "-1", "--format=%B")
        assert f"AConsole-Node: {node}" in body
        assert "AConsole-Agent: claude" in body
        assert f"AConsole-Tab: {tab.id}" in body
        assert "Co-Authored-By: someone <s@e>" in body, "an existing trailer block stays one block"

        params = await _merge_params(client, project, row.branch or "", base, mode)
        result = await client.call("git/mergeWorktree", params, GitOperationResult)
        assert result.operation.status == "finished", result.operation.message
        assert f"AConsole-Node: {node}" in git(repo, "log", "-1", "--format=%B")

        found = await client.call("git/graph", GitGraphParams(project_id=project, plan_node=node), GitGraphResult)
        assert found.nodes
        assert all(str(item.plan_node) == node for item in found.nodes)
        assert all(item.agent == "claude" for item in found.nodes)
        none = await client.call("git/graph", GitGraphParams(project_id=project, plan_node="nd-none"), GitGraphResult)
        assert none.nodes == ()
        await _stop(daemon, task, client)

    asyncio.run(scenario())


@pytest.mark.parametrize(("mode", "first"), [("merge", "pre-merge-commit"), ("squash", "pre-commit")])
def test_a_merge_with_no_worktree_asks_the_hooks(tmp_path: Path, mode: MergeMode, first: str) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        start = commit(repo, "a.txt", "1\n", "first")
        tree = add_worktree(repo, tmp_path / "tree", "work")
        _ = commit(tree, "w.txt", "w\n", "work")
        _ = git(repo, "branch", "release", start)
        _ = commit(repo, "b.txt", "b\n", "base")
        _ = git(repo, "branch", "-f", "release", "HEAD")
        before = git(repo, "rev-parse", "release")
        daemon, task, client, project = await _start(tmp_path, repo)

        _hook(repo, first, "echo 'merge says no' >&2; exit 1")
        params = await _merge_params(client, project, "work", "release", mode)
        refused = await client.call("git/mergeWorktree", params, GitOperationResult)
        assert refused.operation.status == "failed"
        assert "merge says no" in (refused.operation.message or "")
        assert git(repo, "rev-parse", "release") == before

        (repo / ".git" / "hooks" / first).unlink()
        _hook(repo, "commit-msg", "echo 'message says no'; exit 1")
        params = await _merge_params(client, project, "work", "release", mode)
        refused = await client.call("git/mergeWorktree", params, GitOperationResult)
        assert refused.operation.status == "failed"
        assert "message says no" in (refused.operation.message or "")
        assert git(repo, "rev-parse", "release") == before

        # A hook sees the merged tree in the index, and may edit the message, as with a real commit.
        _hook(repo, "commit-msg", 'git ls-files | grep -q w.txt || exit 1; echo "edited by hook" >> "$1"')
        params = await _merge_params(client, project, "work", "release", mode)
        done = await client.call("git/mergeWorktree", params, GitOperationResult)
        assert done.operation.status == "finished", done.operation.message
        assert "edited by hook" in git(repo, "log", "-1", "--format=%B", "release")
        await _stop(daemon, task, client)

    asyncio.run(scenario())
