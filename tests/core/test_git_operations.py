"""Stage, commit, and restore through the real daemon, in real worktrees."""

from __future__ import annotations

import asyncio
from pathlib import Path

import msgspec
import pytest

from aconsole.contracts.app import DiffSummaryResult, ProjectAddParams, ProjectView, TabView
from aconsole.contracts.common import AgentKind, ProjectId, TabStatus, WorkspaceMode
from aconsole.contracts.git import (
    DiffSource,
    GitChangeResult,
    GitCommitParams,
    GitDiffParams,
    GitPathsParams,
    GitStateParams,
    GitStateResult,
    RestoreFileParams,
    RestoreFilePreviewParams,
    RestoreFilePreviewResult,
    WorktreeGitState,
)
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.git_state import blocks_writes, require_fresh
from aconsole.core.domain.tab_status import TabStatus as DomainStatus
from tests.core.git_helpers import commit, git, new_repo
from tests.core.test_wire import _Client, _connect, _daemon, _open, _stop


async def _row(client: _Client, project: ProjectId, path: Path) -> WorktreeGitState:
    state = await client.call("git/state", GitStateParams(project_id=project), GitStateResult)
    return next(item for item in state.worktrees if Path(item.path).samefile(path))  # noqa: ASYNC240


async def _project(tmp_path: Path, repo: Path) -> tuple[object, asyncio.Task[int], _Client, ProjectId]:
    daemon = _daemon(tmp_path, serve_ingest=False)
    task = asyncio.create_task(daemon.serve())
    client = await _connect(daemon.socket)
    project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
    return daemon, task, client, project.id


def test_stage_unstage_commit_keep_staged_and_unstaged_apart(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a.txt", "1\n", "first")
        daemon, task, client, project = await _project(tmp_path, repo)
        (repo / "a.txt").write_text("2\n", encoding="utf-8")
        (repo / "имя файла.txt").write_text("x\n", encoding="utf-8")
        row = await _row(client, project, repo)
        assert (row.staged, row.unstaged, row.untracked) == (0, 1, 1)

        def paths(*names: str, seen: WorktreeGitState) -> GitPathsParams:
            return GitPathsParams(
                project_id=project,
                worktree=seen.path,
                paths=names,
                expect_head=seen.head_oid,
                expect_index=seen.index_digest,
            )

        done = await client.call("git/stage", paths("имя файла.txt", seen=row), GitChangeResult)
        assert done.ok
        row = await _row(client, project, repo)
        assert (row.staged, row.unstaged, row.untracked) == (1, 1, 0)
        staged = await client.call(
            "git/diffSummary",
            GitDiffParams(project_id=project, source=DiffSource(kind="index", worktree=row.path)),
            DiffSummaryResult,
        )
        assert [item.path for item in staged.files] == ["имя файла.txt"]

        done = await client.call("git/stage", paths("a.txt", seen=row), GitChangeResult)
        row = await _row(client, project, repo)
        done = await client.call("git/unstage", paths("a.txt", seen=row), GitChangeResult)
        assert done.ok
        row = await _row(client, project, repo)
        assert (row.staged, row.unstaged) == (1, 1)

        made = await client.call(
            "git/commit",
            GitCommitParams(
                project_id=project,
                worktree=row.path,
                message="add name\n\nbody",
                expect_head=row.head_oid,
                expect_index=row.index_digest,
            ),
            GitChangeResult,
        )
        assert made.ok, made.detail
        assert made.head_oid == git(repo, "rev-parse", "HEAD")
        assert git(repo, "log", "-1", "--format=%s") == "add name"
        assert git(repo, "-c", "core.quotePath=false", "show", "--name-only", "--format=", "HEAD") == "имя файла.txt"
        assert (repo / "a.txt").read_text(encoding="utf-8") == "2\n"
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_a_race_with_an_outside_change_is_refused(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a.txt", "1\n", "first")
        daemon, task, client, project = await _project(tmp_path, repo)
        (repo / "b.txt").write_text("b\n", encoding="utf-8")
        _ = git(repo, "add", "b.txt")
        row = await _row(client, project, repo)

        # Another terminal stages one more file after the user looked.
        (repo / "c.txt").write_text("c\n", encoding="utf-8")
        _ = git(repo, "add", "c.txt")
        stale = await client.error(
            "git/commit",
            GitCommitParams(
                project_id=project,
                worktree=row.path,
                message="m",
                expect_head=row.head_oid,
                expect_index=row.index_digest,
            ),
        )
        assert stale == "GIT_STATE_STALE"
        assert git(repo, "log", "--format=%s") == "first"

        # And a new commit moves HEAD.
        row = await _row(client, project, repo)
        _ = git(repo, "commit", "-qm", "outside")
        stale = await client.error(
            "git/stage",
            GitPathsParams(
                project_id=project,
                worktree=row.path,
                paths=("x",),
                expect_head=row.head_oid,
                expect_index=row.index_digest,
            ),
        )
        assert stale == "GIT_STATE_STALE"
        unknown = await client.error(
            "git/stage",
            GitPathsParams(
                project_id=project,
                worktree=str(tmp_path),
                paths=("x",),
                expect_head=None,
                expect_index="",
            ),
        )
        assert unknown == "GIT_WORKTREE_UNKNOWN"
        outside = await client.error(
            "git/stage",
            GitPathsParams(
                project_id=project,
                worktree=row.path,
                paths=("../../etc/passwd",),
                expect_head=None,
                expect_index="",
            ),
        )
        assert outside == "INVALID_PARAMS"
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_a_refusing_hook_is_an_answer_with_its_words(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a.txt", "1\n", "first")
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho 'lint says no' >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        daemon, task, client, project = await _project(tmp_path, repo)
        (repo / "a.txt").write_text("2\n", encoding="utf-8")
        _ = git(repo, "add", "a.txt")
        row = await _row(client, project, repo)
        made = await client.call(
            "git/commit",
            GitCommitParams(
                project_id=project,
                worktree=row.path,
                message="m",
                expect_head=row.head_oid,
                expect_index=row.index_digest,
            ),
            GitChangeResult,
        )
        assert not made.ok
        assert made.detail is not None
        assert "lint says no" in made.detail
        assert made.head_oid == row.head_oid
        after = await _row(client, project, repo)
        assert after.staged == 1
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_restoring_one_file_keeps_the_rest_and_a_way_back(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        first = commit(repo, "a.txt", "1\n2\n", "first")
        _ = commit(repo, "b.txt", "b\n", "second")
        daemon, task, client, project = await _project(tmp_path, repo)
        (repo / "a.txt").write_text("changed\n", encoding="utf-8")
        (repo / "c.txt").write_text("keep me\n", encoding="utf-8")
        row = await _row(client, project, repo)
        ask = RestoreFilePreviewParams(project_id=project, worktree=row.path, path="a.txt", source_oid=first)
        preview = await client.call("git/restoreFilePreview", ask, RestoreFilePreviewResult)
        assert (preview.insertions, preview.deletions) == (2, 1)
        assert preview.exists_in_source

        wrong = await client.error(
            "git/restoreFile",
            RestoreFileParams(
                project_id=project,
                worktree=row.path,
                path="b.txt",
                source_oid=first,
                confirm_token=preview.confirm_token,
            ),
        )
        assert wrong == "CONFIRM_TOKEN_INVALID"
        done = await client.call(
            "git/restoreFile",
            RestoreFileParams(
                project_id=project,
                worktree=row.path,
                path="a.txt",
                source_oid=first,
                confirm_token=preview.confirm_token,
            ),
            GitChangeResult,
        )
        assert done.ok
        assert (repo / "a.txt").read_text(encoding="utf-8") == "1\n2\n"
        assert (repo / "c.txt").read_text(encoding="utf-8") == "keep me\n"
        safety = git(repo, "for-each-ref", "--format=%(refname)", "refs/aconsole/safety")
        assert safety
        assert git(repo, "show", f"{safety}:a.txt") == "changed"
        again = await client.error(
            "git/restoreFile",
            RestoreFileParams(
                project_id=project,
                worktree=row.path,
                path="a.txt",
                source_oid=first,
                confirm_token=preview.confirm_token,
            ),
        )
        assert again == "CONFIRM_TOKEN_INVALID"

        # A file the source does not hold goes away; the token said so first.
        ask = RestoreFilePreviewParams(project_id=project, worktree=row.path, path="b.txt", source_oid=first)
        preview = await client.call("git/restoreFilePreview", ask, RestoreFilePreviewResult)
        assert not preview.exists_in_source
        done = await client.call(
            "git/restoreFile",
            RestoreFileParams(
                project_id=project,
                worktree=row.path,
                path="b.txt",
                source_oid=first,
                confirm_token=preview.confirm_token,
            ),
            GitChangeResult,
        )
        assert done.ok
        assert not (repo / "b.txt").exists()
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_a_tab_worktree_is_left_alone_while_its_agent_works(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a.txt", "1\n", "first")
        daemon, task, client, project = await _project(tmp_path, repo)
        tab = await client.call(
            "tab/open",
            msgspec.structs.replace(_open(project), workspace=WorkspaceMode.NEW_WORKTREE),
            TabView,
        )
        assert tab.agent is AgentKind.CLAUDE
        tree = Path(tab.worktree_path)
        (tree / "n.txt").write_text("n\n", encoding="utf-8")
        row = await _row(client, project, tree)
        daemon.tabs._tabs[str(tab.id)].status = TabStatus.WORKING
        row = await _row(client, project, tree)
        assert row.busy
        busy = await client.error(
            "git/stage",
            GitPathsParams(
                project_id=project,
                worktree=row.path,
                paths=("n.txt",),
                expect_head=row.head_oid,
                expect_index=row.index_digest,
            ),
        )
        assert busy == "WORKTREE_BUSY"
        daemon.tabs._tabs[str(tab.id)].status = TabStatus.WAITING_INPUT
        done = await client.call(
            "git/stage",
            GitPathsParams(
                project_id=project,
                worktree=row.path,
                paths=("n.txt",),
                expect_head=row.head_oid,
                expect_index=row.index_digest,
            ),
            GitChangeResult,
        )
        assert done.ok

        # The other tab's worktree did not notice.
        assert git(repo, "status", "--porcelain") == ""
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_domain_rules() -> None:
    assert blocks_writes(DomainStatus.WORKING)
    assert not blocks_writes(DomainStatus.IDLE)
    assert not blocks_writes(None)
    with pytest.raises(DomainError, match="GIT_STATE_STALE"):
        require_fresh(expected_head="a", head="b", expected_index="i", index="i")
