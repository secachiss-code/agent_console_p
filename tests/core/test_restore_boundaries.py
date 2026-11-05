"""Restore deletions stay inside opened worktree directories."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aconsole.core.application.ports.git_repo import GitCommandError
from aconsole.core.infrastructure.git.checkpoints import CheckpointGitError, GitCheckpoints
from aconsole.core.infrastructure.git.operations import GitMutations
from tests.core.git_helpers import commit, git, new_repo


@pytest.fixture
def restore_repo(tmp_path: Path) -> tuple[Path, str, Path]:
    repo = new_repo(tmp_path / "repo")
    target = commit(repo, "base.txt", "base\n", "base")
    _ = commit(repo, "sub/victim.txt", "tracked\n", "later")
    outside = tmp_path / "outside"
    outside.mkdir()
    _ = (outside / "victim.txt").write_text("outside sentinel\n")
    return repo, target, outside


def _restore(endpoint: str, repo: Path, target: str) -> None:
    if endpoint == "checkpoint":
        GitCheckpoints().materialize(repo, target)
    else:
        assert GitMutations().restore_file(repo, target, "sub/victim.txt").ok


@pytest.mark.parametrize("endpoint", ["checkpoint", "file"])
def test_restore_refuses_a_symlink_parent(restore_repo: tuple[Path, str, Path], endpoint: str) -> None:
    repo, target, outside = restore_repo
    (repo / "sub/victim.txt").unlink()
    (repo / "sub").rmdir()
    (repo / "sub").symlink_to(outside, target_is_directory=True)
    error = CheckpointGitError if endpoint == "checkpoint" else GitCommandError
    with pytest.raises(error):
        _restore(endpoint, repo, target)
    assert (outside / "victim.txt").read_text() == "outside sentinel\n"


@pytest.mark.parametrize("endpoint", ["checkpoint", "file"])
def test_restore_keeps_an_opened_directory_when_its_name_is_replaced(
    restore_repo: tuple[Path, str, Path], endpoint: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, target, outside = restore_repo
    unlink = os.unlink
    swapped = False

    def replace_parent(path: str | Path, *, dir_fd: int | None = None) -> None:
        nonlocal swapped
        if Path(path).name == "victim.txt" and not swapped:
            swapped = True
            (repo / "sub").rename(repo / "retired")
            (repo / "sub").symlink_to(outside, target_is_directory=True)
        unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", replace_parent)
    _restore(endpoint, repo, target)
    assert swapped
    assert (outside / "victim.txt").read_text() == "outside sentinel\n"
    assert not (repo / "retired/victim.txt").exists()


@pytest.mark.parametrize("endpoint", ["checkpoint", "file"])
def test_restore_unlinks_a_leaf_symlink_without_touching_its_target(
    restore_repo: tuple[Path, str, Path], endpoint: str,
) -> None:
    repo, target, outside = restore_repo
    (repo / "sub/victim.txt").unlink()
    (repo / "sub/victim.txt").symlink_to(outside / "victim.txt")
    _restore(endpoint, repo, target)
    assert not (repo / "sub/victim.txt").is_symlink()
    assert (outside / "victim.txt").read_text() == "outside sentinel\n"
    assert git(repo, "rev-parse", "HEAD") != target
