"""Removing a project from the catalog preserves a kept working copy and its files."""

from __future__ import annotations

import asyncio
from pathlib import Path

from aconsole.contracts.app import (
    CloseTabParams,
    CloseTabResult,
    EmptyResult,
    OpenTabParams,
    ProjectAddParams,
    ProjectIdParams,
    ProjectView,
    TabView,
)
from aconsole.contracts.common import AgentKind, WorkspaceMode
from tests.core.git_helpers import commit
from tests.core.test_wire import _connect, _daemon, _git_repo, _stop


def test_remove_project_keeps_repository_and_worktree_across_restart(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    _ = commit(repo, "base.txt", "initial\n", "base")

    async def scenario() -> Path:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        tab = await client.call("tab/open", OpenTabParams(project_id=project.id, agent=AgentKind.CLAUDE,
            workspace=WorkspaceMode.NEW_WORKTREE, base_branch="trunk"), TabView)
        worktree = Path(tab.worktree_path)
        marker = worktree / "keep-me.txt"
        marker.write_text("unfinished user work")
        _ = await client.call("tab/close", CloseTabParams(tab_id=tab.id, decision="keep"), CloseTabResult)
        _ = await client.call("project/remove", ProjectIdParams(project_id=project.id), EmptyResult)
        assert (await client.snapshot()).projects == ()
        await _stop(daemon, task, client)
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        assert (await client.snapshot()).projects == ()
        assert marker.read_text() == "unfinished user work"
        assert (repo / ".git").is_dir()
        assert (worktree / ".git").is_file()
        restored = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        assert restored.root == str(repo)
        await _stop(daemon, task, client)
        return worktree

    assert asyncio.run(scenario()).is_dir()
