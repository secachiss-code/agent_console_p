"""`git/*` through the assembled daemon, on real repositories."""

from __future__ import annotations

import asyncio
from pathlib import Path

import msgspec

from aconsole.contracts.app import DiffFileResult, DiffSummaryResult, ProjectAddParams, ProjectView, TabView
from aconsole.contracts.common import WorkspaceMode
from aconsole.contracts.git import (
    DiffSource,
    GitDiffFileParams,
    GitDiffParams,
    GitGraphParams,
    GitGraphResult,
    GitStateParams,
    GitStateResult,
)
from tests.core.git_helpers import commit, git, new_repo
from tests.core.test_wire import _connect, _daemon, _open, _stop


def test_state_and_graph_of_a_real_project(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo", branch="develop")
        start = commit(repo, "a.txt", "1\n", "first")
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        tab = await client.call(
            "tab/open", msgspec.structs.replace(_open(project.id), workspace=WorkspaceMode.NEW_WORKTREE), TabView,
        )

        state = await client.call("git/state", GitStateParams(project_id=project.id), GitStateResult)
        assert state.base_branch == "develop"
        assert state.base_oid == start
        row = next(item for item in state.worktrees if item.tab_id == tab.id)
        assert row.start_oid == start
        assert row.merge.status == "not_applicable"
        tree = row.path

        # Behind, conflicted, and dirty are three separate facts.
        done = commit(Path(tree), "a.txt", "agent\n", "agent work")
        moved = commit(repo, "a.txt", "base\n", "base work")
        (Path(tree) / "new.txt").write_text("n\n", encoding="utf-8")
        state = await client.call("git/state", GitStateParams(project_id=project.id), GitStateResult)
        row = next(item for item in state.worktrees if item.tab_id == tab.id)
        assert (row.ahead, row.behind, row.untracked, row.dirty) == (1, 1, 1, True)
        assert row.merge.status == "conflict"
        assert row.merge.conflicts == ("a.txt",)
        assert row.merge.source_oid == done
        assert row.merge.target_oid == moved
        assert state.empty is False

        graph = await client.call("git/graph", GitGraphParams(project_id=project.id, limit=2), GitGraphResult)
        assert len(graph.nodes) == 2
        assert graph.next_cursor is not None
        rest = await client.call(
            "git/graph",
            GitGraphParams(project_id=project.id, limit=2, cursor=graph.next_cursor),
            GitGraphResult,
        )
        assert [node.oid for node in rest.nodes] == [start]
        assert rest.next_cursor is None
        by_oid = {node.oid: node for node in graph.nodes}
        assert by_oid[done].tab_id == tab.id
        assert any(ref.kind == "base" for ref in by_oid[moved].refs)

        # A moved tip makes an old cursor useless, and the client is told.
        _ = commit(repo, "c.txt", "1\n", "more")
        stale = await client.error(
            "git/graph", GitGraphParams(project_id=project.id, limit=2, cursor=graph.next_cursor),
        )
        assert stale == "GIT_SNAPSHOT_STALE"

        summary = await client.call(
            "git/diffSummary",
            GitDiffParams(project_id=project.id, source=DiffSource(kind="commit", oid=done)),
            DiffSummaryResult,
        )
        assert [item.path for item in summary.files] == ["a.txt"]
        hunks = await client.call(
            "git/diffFile",
            GitDiffFileParams(project_id=project.id, source=DiffSource(kind="commit", oid=done), path="a.txt"),
            DiffFileResult,
        )
        assert hunks.hunks
        unknown = await client.error(
            "git/diffSummary",
            GitDiffParams(project_id=project.id, source=DiffSource(kind="worktree", worktree=str(tmp_path))),
        )
        assert unknown == "GIT_WORKTREE_UNKNOWN"
        bad = await client.error(
            "git/diffSummary",
            GitDiffParams(project_id=project.id, source=DiffSource(kind="commit", oid="--output=/tmp/x")),
        )
        assert bad == "INVALID_PARAMS"
        assert git(repo, "status", "--porcelain") == ""
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_external_change_reaches_subscribers(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a.txt", "1\n", "first")
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        _ = await client.call("git/state", GitStateParams(project_id=project.id), GitStateResult)

        (repo / "z.txt").write_text("z\n", encoding="utf-8")
        assert await asyncio.to_thread(daemon.git.announce, str(project.id))
        assert not await asyncio.to_thread(daemon.git.announce, str(project.id))
        await _stop(daemon, task, client)

    asyncio.run(scenario())
