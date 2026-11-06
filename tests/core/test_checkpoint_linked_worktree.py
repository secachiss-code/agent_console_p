"""Starting an agent snapshots a linked worktree with its runtime lock present."""

from typing import TYPE_CHECKING

import pytest

from aconsole.core.infrastructure.git.checkpoints import GitCheckpoints
from tests.core.test_checkpoints_git import _commit_base, _git, _show

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("ignored", [True, False])
def test_checkpoint_in_linked_worktree_with_ignored_runtime_lock(tmp_path: Path, *, ignored: bool) -> None:
    repo = tmp_path / "repo"
    _commit_base(repo)
    with (repo / ".gitignore").open("a", encoding="utf-8") as stream:
        _ = stream.write(".aconsole/\n" if ignored else "\n")
    _ = _git(repo, ("add", ".gitignore"))
    _ = _git(repo, ("commit", "-m", "Ignore runtime state"))
    worktree = tmp_path / "worker"
    _ = _git(repo, ("worktree", "add", "-b", "worker", str(worktree)))
    (worktree / ".aconsole").mkdir()
    _ = (worktree / ".aconsole/lock").write_text("runtime lock\n", encoding="utf-8")
    _ = (worktree / "a.txt").write_text("agent input\n", encoding="utf-8")
    ref = "refs/aconsole/sessions/test/checkpoints/1"
    index_before = _git(worktree, ("ls-files", "--stage"))
    captured = GitCheckpoints().capture(worktree, ref, "Begin")
    assert _show(repo, ref, "a.txt") == "agent input\n"
    assert ".aconsole/lock" not in captured.files
    assert (worktree / ".aconsole/lock").read_text(encoding="utf-8") == "runtime lock\n"
    assert _git(worktree, ("ls-files", "--stage")) == index_before
