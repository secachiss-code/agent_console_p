"""What follows a merge, on the real daemon: the plan, the worktree, and facts."""

from __future__ import annotations

import asyncio
from pathlib import Path

import msgspec
import pytest

from aconsole.contracts.app import ProjectAddParams, ProjectView, TabView
from aconsole.contracts.common import AgentKind, ProjectId, TabStatus, WorkspaceMode
from aconsole.contracts.git import (
    CleanupWorktreeParams,
    CleanupWorktreeResult,
    FactsToReviewResult,
    GitOperationResult,
    GitStateParams,
    MergeMode,
    MergePreviewParams,
    MergePreviewResult,
    MergeWorktreeParams,
)
from aconsole.core.domain.git_state import ref_path, touched_refs
from aconsole.core.domain.plan import NodeStatus, after_merge
from aconsole.core.infrastructure.mcp.tools import NodeIn, TabContext
from tests.core.git_helpers import add_worktree, commit, git, new_repo
from tests.core.test_wire import _Client, _connect, _daemon, _open, _stop


async def _start(tmp_path: Path, repo: Path) -> tuple[object, asyncio.Task[int], _Client, ProjectId]:
    daemon = _daemon(tmp_path, serve_ingest=False)
    task = asyncio.create_task(daemon.serve())
    client = await _connect(daemon.socket)
    project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
    return daemon, task, client, project.id


async def _merge(
    client: _Client, project: ProjectId, source: str, target: str, mode: MergeMode = "merge"
) -> GitOperationResult:
    preview = await client.call(
        "git/mergePreview", MergePreviewParams(project_id=project, source=source, target=target), MergePreviewResult
    )
    params = MergeWorktreeParams(
        project_id=project,
        source=preview.source,
        target=preview.target,
        source_oid=preview.source_oid,
        target_oid=preview.target_oid,
        mode=mode,
        confirm_token=preview.confirm_token,
    )
    return await client.call("git/mergeWorktree", params, GitOperationResult)


def _node(key: str, *, review: bool = False) -> NodeIn:
    return NodeIn(key=key, title=f"node {key}", objective="do it", review_required=review)


@pytest.mark.parametrize(
    ("review", "expected"),
    [(False, NodeStatus.DONE.value), (True, NodeStatus.VERIFYING.value)],
)
def test_a_merge_moves_its_plan_node_only_as_far_as_review_allows(tmp_path: Path, review: bool, expected: str) -> None:  # noqa: FBT001
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a.txt", "1\n", "first")
        base = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        daemon, task, client, project = await _start(tmp_path, repo)
        tab = await client.call(
            "tab/open", msgspec.structs.replace(_open(project), workspace=WorkspaceMode.NEW_WORKTREE), TabView
        )
        assert tab.agent is AgentKind.CLAUDE
        tree = Path(tab.worktree_path)
        _ = commit(tree, "w.txt", "w\n", "work")
        ctx = TabContext(tab_id=str(tab.id), project_id=str(project), worktree=tree, agent="claude")
        board = daemon._plans
        created = board.create(ctx, "plan", "goal", [_node("AB-1", review=review)])
        node_id = created.nodes[0].node_id
        _ = board.claim(ctx, node_id)
        result = await _merge(client, project, git(tree, "rev-parse", "--abbrev-ref", "HEAD"), base)
        assert result.operation.status == "finished", result.operation.message
        oid = result.operation.result_oid
        node = next(item for item in board.view(created.plan_id).nodes if str(item.id) == node_id)
        assert node.status.value == expected
        stored = board._plans[created.plan_id]
        assert ("commit", oid) in next(iter(stored.meta.values())).artifacts
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_a_merged_worktree_goes_and_an_unmerged_one_stays(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a.txt", "1\n", "first")
        base = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        tree = add_worktree(repo, tmp_path / "tree", "work")
        _ = commit(tree, "w.txt", "w\n", "work")
        daemon, task, client, project = await _start(tmp_path, repo)
        ask = CleanupWorktreeParams(project_id=project, worktree=str(tree))
        assert await client.error("git/cleanupWorktree", ask) == "WORKTREE_NOT_REMOVABLE"
        assert tree.exists()

        main = CleanupWorktreeParams(project_id=project, worktree=str(repo))
        assert await client.error("git/cleanupWorktree", main) == "WORKTREE_NOT_REMOVABLE"

        _ = await _merge(client, project, "work", base)
        (tree / "wip.txt").write_text("not committed\n", encoding="utf-8")
        assert await client.error("git/cleanupWorktree", ask) == "WORKTREE_NOT_REMOVABLE"
        (tree / "wip.txt").unlink()

        done = await client.call("git/cleanupWorktree", ask, CleanupWorktreeResult)
        assert done.removed
        assert not done.branch_deleted, "the user named this branch"
        assert done.kept_ref is None
        assert not tree.exists()
        assert git(repo, "rev-parse", "--verify", "work")
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_a_squashed_worktree_keeps_its_commits_on_a_side_ref(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a.txt", "1\n", "first")
        base = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        daemon, task, client, project = await _start(tmp_path, repo)
        tab = await client.call(
            "tab/open", msgspec.structs.replace(_open(project), workspace=WorkspaceMode.NEW_WORKTREE), TabView
        )
        tree = Path(tab.worktree_path)
        tip = commit(tree, "w.txt", "w\n", "work")
        branch = git(tree, "rev-parse", "--abbrev-ref", "HEAD")
        assert branch.startswith("ac/")
        result = await _merge(client, project, branch, base, "squash")
        assert result.operation.status == "finished", result.operation.message

        ask = CleanupWorktreeParams(project_id=project, worktree=str(tree))
        assert await client.error("git/cleanupWorktree", ask) == "WORKTREE_NOT_REMOVABLE", "its tab is open"

        daemon.tabs._tabs[str(tab.id)].status = TabStatus.EXITED
        done = await client.call("git/cleanupWorktree", ask, CleanupWorktreeResult)
        assert done.removed
        assert done.branch_deleted
        assert done.kept_ref is not None
        assert git(repo, "rev-parse", done.kept_ref) == tip
        assert str(tab.id) not in daemon.tabs._tabs
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_ignored_files_are_deleted_only_when_the_caller_says_so(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, ".gitignore", "*.log\n", "first")
        tree = add_worktree(repo, tmp_path / "tree", "gone")
        (tree / "x.log").write_text("log\n", encoding="utf-8")
        daemon, task, client, project = await _start(tmp_path, repo)
        ask = CleanupWorktreeParams(project_id=project, worktree=str(tree))
        assert await client.error("git/cleanupWorktree", ask) == "WORKTREE_NOT_REMOVABLE"
        assert (tree / "x.log").exists()
        sure = CleanupWorktreeParams(project_id=project, worktree=str(tree), discard_ignored=True)
        done = await client.call("git/cleanupWorktree", sure, CleanupWorktreeResult)
        assert done.removed
        assert not tree.exists()
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_a_fact_needs_a_look_only_when_its_own_code_moved(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "src/a.py", "1\n", "first")
        _ = commit(repo, "src/b.py", "1\n", "second")
        daemon, task, client, project = await _start(tmp_path, repo)
        tab = await client.call("tab/open", _open(project), TabView)
        ctx = TabContext(tab_id=str(tab.id), project_id=str(project), worktree=repo, agent="claude")
        board = daemon._plans
        name = "decision"
        a_fact = board.kb_add(ctx, name, "about a", "a is fine", "", ["src/a.py:1"], None)
        _ = board.kb_add(ctx, name, "about b", "b is fine", "", ["src/b.py"], None)
        ask = GitStateParams(project_id=project)
        none = await client.call("git/factsToReview", ask, FactsToReviewResult)
        assert none.facts == ()

        _ = commit(repo, "src/a.py", "2\n", "a moves")
        found = await client.call("git/factsToReview", ask, FactsToReviewResult)
        assert [item.id for item in found.facts] == [a_fact.kb_id]
        assert found.facts[0].changed_paths == ("src/a.py",)
        assert found.note_path is None, "no vault set yet"

        vault = tmp_path / "vault"
        vault.mkdir()
        daemon._to_vault(daemon.vault.use_root, vault)
        found = await client.call("git/factsToReview", ask, FactsToReviewResult)
        assert found.note_path is not None
        note = Path(found.note_path)
        async with asyncio.timeout(5):
            while not note.exists():  # noqa: ASYNC110, ASYNC240 -- the vault thread writes this
                await asyncio.sleep(0.02)
        text = note.read_text(encoding="utf-8")  # noqa: ASYNC240
        assert "about a: src/a.py" in text
        assert "about b" not in text
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_domain_rules() -> None:
    assert after_merge(NodeStatus.IN_PROGRESS, review_required=False) is NodeStatus.DONE
    assert after_merge(NodeStatus.IN_PROGRESS, review_required=True) is NodeStatus.VERIFYING
    assert after_merge(NodeStatus.VERIFYING, review_required=False) is NodeStatus.DONE
    assert after_merge(NodeStatus.VERIFYING, review_required=True) is None
    assert after_merge(NodeStatus.DONE, review_required=False) is None
    assert ref_path("./src/a.py:10") == "src/a.py"
    assert ref_path("src/a.py#name") == "src/a.py"
    assert touched_refs(("src/",), ("src/x/y.py", "srcs/z.py")) == ("src/x/y.py",)
