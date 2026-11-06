"""Repo configuration cannot hide unsaved submodule bytes from cleanup checks."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from aconsole.contracts.app import ProjectAddParams, ProjectView
from aconsole.contracts.git import CleanupWorktreeParams
from aconsole.contracts.work_sessions import WorkSessionCreateParams, WorkSessionView
from aconsole.core.infrastructure.git.history import GitHistory
from aconsole.core.infrastructure.git.worktrees import WorktreeRequest, Worktrees
from tests.core.git_helpers import commit, git, new_repo
from tests.core.test_wire import _connect, _daemon, _stop


def _repository(tmp_path: Path) -> Path:
    module = new_repo(tmp_path / "module")
    _ = commit(module, "tracked", "baseline\n", "module seed")
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, ".gitignore", "cache/\n", "seed")
    _ = git(repo, "-c", "protocol.file.allow=always", "submodule", "add", str(module), "sub")
    _ = git(repo, "commit", "-qam", "add module")
    return repo


def _local_work(repo: Path, dest: Path, key: str, value: str, *, untracked: bool) -> Path:
    _ = git(dest, "-c", "protocol.file.allow=always", "submodule", "update", "--init")
    protected_file = dest / "sub" / ("new-file" if untracked else "tracked")
    protected_file.write_text("unsaved work\n", encoding="utf-8")
    (dest / "cache").mkdir()
    (dest / "cache/discardable").write_text("ignored cache", encoding="utf-8")
    _ = git(repo, "config", key, value)
    return protected_file


@pytest.mark.parametrize(("key", "value", "untracked"), [
    ("submodule.sub.ignore", "all", False),
    ("submodule.sub.ignore", "dirty", False),
    ("submodule.sub.ignore", "untracked", True),
    ("diff.ignoreSubmodules", "all", False),
])
def test_status_and_managed_close_expose_hidden_submodule_work(
    tmp_path: Path, key: str, value: str, *, untracked: bool,
) -> None:
    repo = _repository(tmp_path)
    trees = Worktrees(Path("/usr/bin/git"))
    dest = tmp_path / "worker"
    created = trees.create(WorktreeRequest(repo=repo, dest=dest, slug="test", tab_id="submodule"))
    trees.release(dest)
    protected_file = _local_work(repo, dest, key, value, untracked=untracked)
    status = GitHistory().status(dest)
    assert status.unstaged > 0
    assert trees.state(repo, dest) == "dirty"
    result = trees.close(repo, dest, discard_ignored=True)
    assert not result.removed
    assert result.reason == "dirty"
    assert protected_file.read_text(encoding="utf-8") == "unsaved work\n"
    assert git(repo, "rev-parse", created.branch)


@pytest.mark.parametrize("key", ["submodule.sub.ignore", "diff.ignoreSubmodules"])
def test_public_git_cleanup_preserves_submodule_work_when_discarding_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str,
) -> None:
    repo = _repository(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FAKE_HOME", str(home))

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        serving = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            session = await client.call("workSession/create", WorkSessionCreateParams(
                project_id=project.id, title="Keep submodule work",
            ), WorkSessionView)
            protected_file = _local_work(repo, Path(session.worktree), key, "all", untracked=False)
            assert await client.error("git/cleanupWorktree", CleanupWorktreeParams(
                project_id=project.id, worktree=session.worktree, discard_ignored=True,
            )) == "WORKTREE_NOT_REMOVABLE"
            assert protected_file.read_text(encoding="utf-8") == "unsaved work\n"
            assert git(repo, "rev-parse", session.branch or "")
        finally:
            await _stop(daemon, serving, client)

    asyncio.run(scenario())
