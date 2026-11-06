"""Discarding ignored files must keep Git's dirty-worktree refusal enabled."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from aconsole.core.infrastructure.git.operations import GitMutations
from aconsole.core.infrastructure.git.worktrees import WorktreeRequest, Worktrees
from tests.core.git_helpers import commit, git, new_repo

if TYPE_CHECKING:
    from collections.abc import Mapping

    from aconsole.core.infrastructure.git.runner import GitOutput


def _workspace(tmp_path: Path) -> tuple[Path, Path, Worktrees]:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "base", "initial\n", "seed")
    _ = commit(repo, ".gitignore", "cache/\n", "ignore cache")
    trees = Worktrees(Path("/usr/bin/git"))
    dest = tmp_path / "worker"
    _ = trees.create(WorktreeRequest(repo=repo, dest=dest, slug="test", tab_id="discard"))
    trees.release(dest)
    return repo, dest, trees


def _cache(dest: Path) -> None:
    (dest / "cache").mkdir()
    (dest / "cache/data").write_text("approved ignored data", encoding="utf-8")


@pytest.mark.parametrize("adapter", ["worktrees", "mutations"])
@pytest.mark.parametrize("kind", ["untracked", "tracked", "nested_repo", "hidden_untracked"])
def test_a_late_nonignored_write_is_preserved_when_discarding_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter: str, kind: str,
) -> None:
    repo, dest, trees = _workspace(tmp_path)
    _cache(dest)
    if kind == "hidden_untracked":
        _ = git(repo, "config", "status.showUntrackedFiles", "no")
    writer = GitMutations()
    runner = trees._runner if adapter == "worktrees" else writer._git
    execute = runner._execute
    protected_file = dest / ("base" if kind == "tracked" else "late-data")
    inserted = []

    def external_write(
        argv: tuple[str, ...], stdin: bytes | None, env: Mapping[str, str], limit: float,
    ) -> GitOutput:
        if "worktree" in argv and "remove" in argv:
            if kind == "nested_repo":
                nested = new_repo(dest / "nested")
                _ = commit(nested, "local", "separate repository\n", "nested seed")
            protected_file.write_text("new ordinary data\n", encoding="utf-8")
            inserted.append(True)
        return execute(argv, stdin, env, limit)

    monkeypatch.setattr(runner, "_execute", external_write)
    if adapter == "worktrees":
        removed = trees.close(repo, dest, discard_ignored=True).removed
    else:
        removed = writer.remove_worktree(repo, dest, discard_ignored=True).ok
    assert inserted
    assert not removed
    assert protected_file.read_text(encoding="utf-8") == "new ordinary data\n"
    assert git(dest, "rev-parse", "HEAD")
    if kind == "nested_repo":
        assert git(dest / "nested", "show", "HEAD:local") == "separate repository"


@pytest.mark.parametrize("adapter", ["worktrees", "mutations"])
def test_explicit_discard_removes_a_clean_worktree_with_only_ignored_cache(tmp_path: Path, adapter: str) -> None:
    repo, dest, trees = _workspace(tmp_path)
    _cache(dest)
    if adapter == "worktrees":
        removed = trees.close(repo, dest, discard_ignored=True).removed
    else:
        removed = GitMutations().remove_worktree(repo, dest, discard_ignored=True).ok
    assert removed
    assert not dest.exists()


@pytest.mark.parametrize("adapter", ["worktrees", "mutations"])
def test_ignored_data_requires_consent_even_at_the_writer_boundary(tmp_path: Path, adapter: str) -> None:
    repo, dest, trees = _workspace(tmp_path)
    _cache(dest)
    if adapter == "worktrees":
        removed = trees.close(repo, dest).removed
    else:
        removed = GitMutations().remove_worktree(repo, dest, discard_ignored=False).ok
    assert not removed
    assert (dest / "cache/data").read_text(encoding="utf-8") == "approved ignored data"


@pytest.mark.parametrize("adapter", ["worktrees", "mutations"])
def test_the_runtime_lock_alone_does_not_prevent_normal_cleanup(tmp_path: Path, adapter: str) -> None:
    repo, dest, trees = _workspace(tmp_path)
    if adapter == "worktrees":
        removed = trees.close(repo, dest).removed
    else:
        removed = GitMutations().remove_worktree(repo, dest, discard_ignored=False).ok
    assert removed
    assert not dest.exists()
