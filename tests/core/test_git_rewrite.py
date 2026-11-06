"""Rebase, cherry-pick, and sending a diff, through the real daemon in real repositories."""

from __future__ import annotations

import asyncio
lazy from pathlib import Path

import msgspec

from aconsole.contracts.app import CommandResult, ProjectAddParams, ProjectView, TabView
from aconsole.contracts.common import ProjectId, TabStatus, WorkspaceMode
from aconsole.contracts.events import InjectToTerminal
from aconsole.contracts.git import (
    CherryPickParams,
    DiffSource,
    GitChangeResult,
    GitOperationResult,
    GitStateParams,
    GitStateResult,
    RebaseParams,
    SendDiffParams,
    SendDiffPreviewParams,
    SendDiffPreviewResult,
    WorktreeOperationParams,
)
from aconsole.core.domain.git_state import clean_for_terminal, count_patch_files
from tests.core.git_helpers import add_worktree, commit, git, new_repo
from tests.core.test_wire import _Client, _connect, _daemon, _open, _stop


async def _start(tmp_path: Path, repo: Path) -> tuple[object, asyncio.Task[int], _Client, ProjectId]:
    daemon = _daemon(tmp_path, serve_ingest=False)
    task = asyncio.create_task(daemon.serve())
    client = await _connect(daemon.socket)
    project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
    return daemon, task, client, project.id


def _rebase(project: ProjectId, tree: Path, onto: str) -> RebaseParams:
    return RebaseParams(
        project_id=project, worktree=str(tree), onto_oid=onto, expect_head=git(tree, "rev-parse", "HEAD")
    )


def test_a_clean_rebase_keeps_the_old_head(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a.txt", "1\n", "first")
        tree = add_worktree(repo, tmp_path / "tree", "work")
        old = commit(tree, "w.txt", "w\n", "work")
        onto = commit(repo, "b.txt", "b\n", "base")
        daemon, task, client, project = await _start(tmp_path, repo)
        result = await client.call("git/rebase", _rebase(project, tree, onto), GitOperationResult)
        operation = result.operation
        assert operation.status == "finished", operation.message
        assert git(tree, "rev-parse", "HEAD~1") == onto
        prefix = (
            f"refs/aconsole/sessions/{operation.work_session_id}/before"
            if operation.work_session_id is not None else "refs/aconsole/before"
        )
        assert git(repo, "rev-parse", f"{prefix}/{operation.id}") == old

        stale = RebaseParams(project_id=project, worktree=str(tree), onto_oid=onto, expect_head=old)
        assert await client.error("git/rebase", stale) == "GIT_STATE_STALE"
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def _conflicting(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "a.txt", "1\n", "first")
    tree = add_worktree(repo, tmp_path / "tree", "work")
    _ = commit(tree, "a.txt", "work\n", "work side")
    onto = commit(repo, "a.txt", "base\n", "base side")
    return repo, tree, onto


def test_a_conflicted_rebase_waits_and_continue_finishes_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo, tree, onto = _conflicting(tmp_path)
        daemon, task, client, project = await _start(tmp_path, repo)
        result = await client.call("git/rebase", _rebase(project, tree, onto), GitOperationResult)
        assert result.operation.status == "conflicted"
        state = await client.call("git/state", GitStateParams(project_id=project), GitStateResult)
        assert [item.id for item in state.operations] == [result.operation.id]
        row = next(item for item in state.worktrees if item.branch is None or item.path.endswith("tree"))
        assert row.in_progress == "rebase"
        assert row.conflicted == 1

        ask = WorktreeOperationParams(project_id=project, worktree=str(tree))
        early = await client.call("git/continue", ask, GitChangeResult)
        assert not early.ok, "a conflict is the person's to fix"

        (tree / "a.txt").write_text("both\n", encoding="utf-8")
        _ = git(tree, "add", "a.txt")
        done = await client.call("git/continue", ask, GitChangeResult)
        assert done.ok, done.detail
        assert git(tree, "rev-parse", "HEAD~1") == onto
        assert (tree / "a.txt").read_text(encoding="utf-8") == "both\n"
        state = await client.call("git/state", GitStateParams(project_id=project), GitStateResult)
        assert state.operations == ()
        again = await client.error("git/continue", ask)
        assert again == "GIT_OPERATION_NOT_FOUND"
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_abort_puts_the_branch_back(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo, tree, onto = _conflicting(tmp_path)
        before = git(tree, "rev-parse", "HEAD")
        daemon, task, client, project = await _start(tmp_path, repo)
        result = await client.call("git/rebase", _rebase(project, tree, onto), GitOperationResult)
        assert result.operation.status == "conflicted"
        ask = WorktreeOperationParams(project_id=project, worktree=str(tree))
        done = await client.call("git/abort", ask, GitChangeResult)
        assert done.ok
        assert done.head_oid == before
        assert (tree / "a.txt").read_text(encoding="utf-8") == "work\n"
        state = await client.call("git/state", GitStateParams(project_id=project), GitStateResult)
        assert state.operations == ()
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_a_conflict_finished_in_a_terminal_is_noticed(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo, tree, onto = _conflicting(tmp_path)
        daemon, task, client, project = await _start(tmp_path, repo)
        result = await client.call("git/rebase", _rebase(project, tree, onto), GitOperationResult)
        assert result.operation.status == "conflicted"
        _ = git(tree, "rebase", "--abort")
        merges = daemon.git.merges
        assert merges is not None
        await asyncio.to_thread(merges.retry)
        state = await client.call("git/state", GitStateParams(project_id=project), GitStateResult)
        assert state.operations == ()
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_cherry_pick_takes_one_commit(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a.txt", "1\n", "first")
        tree = add_worktree(repo, tmp_path / "tree", "work")
        one = commit(tree, "one.txt", "1\n", "one")
        _ = commit(tree, "two.txt", "2\n", "two")
        daemon, task, client, project = await _start(tmp_path, repo)
        pick = CherryPickParams(
            project_id=project, worktree=str(repo), oid=one, expect_head=git(repo, "rev-parse", "HEAD")
        )
        result = await client.call("git/cherryPick", pick, GitOperationResult)
        assert result.operation.status == "finished", result.operation.message
        assert (repo / "one.txt").exists()
        assert not (repo / "two.txt").exists()
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_a_diff_goes_to_the_named_tab_at_the_size_it_said(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        first = commit(repo, "a.txt", "1\n", "first")
        tip = commit(repo, "a.txt", "payload\x1b[201~\n", "second")
        daemon, task, client, project = await _start(tmp_path, repo)
        tab = await client.call(
            "tab/open", msgspec.structs.replace(_open(project), workspace=WorkspaceMode.NEW_WORKTREE), TabView
        )
        daemon.tabs._tabs[str(tab.id)].status = TabStatus.IDLE
        source = DiffSource(kind="between", oid=tip, against=first)
        preview = await client.call(
            "git/sendDiffPreview",
            SendDiffPreviewParams(project_id=project, source=source, to_tab=tab.id),
            SendDiffPreviewResult,
        )
        assert preview.to_title == tab.title
        assert preview.files == 1
        assert preview.byte_count > 0

        seen: list[InjectToTerminal] = []
        previous = daemon.review._diff._events

        class _Spy:
            def emit(self, event: object) -> None:
                if isinstance(event, InjectToTerminal):
                    seen.append(event)
                previous.emit(event)

        daemon.review._diff._events = _Spy()
        sent = await client.call(
            "git/sendDiff",
            SendDiffParams(project_id=project, source=source, to_tab=tab.id, confirm_token=preview.confirm_token),
            CommandResult,
        )
        assert sent.ok
        assert [str(item.tab_id) for item in seen] == [str(tab.id)]
        assert "payload[201~" in seen[0].text
        assert "\x1b" not in seen[0].text
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_too_much_text_is_refused_with_its_size(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        first = commit(repo, "a.txt", "1\n", "first")
        tip = commit(repo, "a.txt", "x" * 70_000 + "\n", "big")
        daemon, task, client, project = await _start(tmp_path, repo)
        tab = await client.call("tab/open", _open(project), TabView)
        source = DiffSource(kind="between", oid=tip, against=first)
        ask = SendDiffPreviewParams(project_id=project, source=source, to_tab=tab.id)
        assert await client.error("git/sendDiffPreview", ask) == "DIFF_TOO_LARGE"
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_domain_rules() -> None:
    assert clean_for_terminal("a\tb\nc\x1b[0m\x07\x9b") == "a\tb\nc[0m"
    assert count_patch_files("diff --git a b\n+x\ndiff --git c d\n") == 2
