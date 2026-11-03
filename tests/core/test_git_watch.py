"""The core hears Git instead of asking it: updates come from writes, and an idle project runs no git at all."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aconsole.contracts.app import ProjectAddParams, ProjectView
from aconsole.contracts.events import GitStateUpdated, UiEvent
from aconsole.contracts.git import GitStateParams, GitStateResult
from aconsole.core.infrastructure.git.watch import means_git_changed
from tests.core.git_helpers import add_worktree, commit, git, new_repo
from tests.core.test_wire import _connect, _daemon, _stop

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_IDLE_S = 4.0
_WAIT_S = 5.0


async def _until(seen: list[GitStateUpdated], count: int) -> None:
    async with asyncio.timeout(_WAIT_S):
        while len(seen) < count:  # noqa: ASYNC110 -- another thread appends
            await asyncio.sleep(0.02)


def test_outside_commits_and_worktrees_are_heard_and_idle_runs_no_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace = tmp_path / "trace.json"
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))

    def spawned() -> int:
        return trace.read_text(encoding="utf-8").count('"event":"start"') if trace.exists() else 0

    async def scenario() -> None:
        repo = new_repo(tmp_path / "repo")
        _ = commit(repo, "a.txt", "1\n", "first")
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        seen: list[GitStateUpdated] = []
        emit = daemon.git._emit

        def spy(event: UiEvent) -> None:
            if isinstance(event, GitStateUpdated):
                seen.append(event)
            emit(event)

        daemon.git._emit = spy
        _ = await client.call("git/state", GitStateParams(project_id=project.id), GitStateResult)
        await asyncio.sleep(0.5)

        before = spawned()
        await asyncio.sleep(_IDLE_S)
        assert spawned() == before, "nothing changed, so nothing should have run"

        (repo / "a.txt").write_text("2\n", encoding="utf-8")
        _ = git(repo, "add", "a.txt")
        await _until(seen, 1)

        # A burst is one look.
        count = len(seen)
        for number in range(5):
            _ = commit(repo, f"b{number}.txt", "b\n", f"burst {number}")
        await _until(seen, count + 1)
        await asyncio.sleep(0.8)
        assert len(seen) <= count + 2

        # The first linked worktree brings a directory that was not there to watch. Then its own HEAD counts.
        count = len(seen)
        tree = await asyncio.to_thread(add_worktree, repo, tmp_path / "tree", "work")
        await _until(seen, count + 1)
        await asyncio.sleep(0.5)
        count = len(seen)
        _ = await asyncio.to_thread(commit, tree, "w.txt", "w\n", "from the worktree")
        await _until(seen, count + 1)
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_what_counts_as_a_change(tmp_path: Path) -> None:
    common = tmp_path / ".git"
    for path in ("HEAD", "index", "packed-refs", "refs/heads/x", "worktrees/a/HEAD", "worktrees/a/index", "MERGE_HEAD"):
        assert means_git_changed(common, str(common / path)), path
    for path in ("index.lock", "objects/ab/cd", "logs/HEAD", "worktrees/a/logs/HEAD", "COMMIT_EDITMSG", "refs/x.lock"):
        assert not means_git_changed(common, str(common / path)), path
    assert not means_git_changed(common, str(tmp_path / "other"))
