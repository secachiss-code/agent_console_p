"""Worktrees on a temporary repository, and the distrobox.ini confirm token."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

import pytest

from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.containers.distrobox_ini import DistroboxIni, IniError, assemble_argv, ini_diff
from aconsole.core.infrastructure.git.worktrees import (
    CloseResult,
    Created,
    WorktreeError,
    WorktreeRequest,
    Worktrees,
    _status_dirty,
)

_GIT = Path("/usr/bin/git")
_ROOT = Path(__file__).resolve().parents[2]
_INI = _ROOT / "tests" / "fixtures" / "distrobox"
_EMAIL = "aconsole@localhost"
_NAME = "aconsole"


def test_worktrees_create_lock_include_and_status_lines(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    _ = (repo / "local.env").write_text("secret\n", encoding="utf-8")
    (repo / "link.env").symlink_to(repo / "README")
    _ = (repo / ".worktreeinclude").write_text("# skip\n\nlocal.env\nlink.env\n", encoding="utf-8")
    dest = tmp_path / "wt"
    created = _worktrees().create(_request(repo, dest, "agent", "tab1"))
    assert created == Created(
        dest=dest.resolve(),
        branch="ac/agent-tab1",
        base=created.base,
        copied=("local.env",),
    )
    assert (dest / "local.env").read_text(encoding="utf-8") == "secret\n"
    assert not (dest / "link.env").exists()
    assert (dest / "README").read_text(encoding="utf-8") == "hi\n"
    assert _worktrees().locked(dest) is True
    assert _status_dirty("") is False
    assert _status_dirty("?? .aconsole/lock") is False
    assert _status_dirty("?? extra.txt") is True
    assert _status_dirty("R  old -> new") is True
    assert _status_dirty("x") is True
    held = _worktrees().close(repo, dest)
    assert held == CloseResult(removed=False, reason="locked")
    assert dest.is_dir()


def test_worktrees_keep_dirty_and_ahead_remove_clean_and_prune(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    trees = _worktrees()
    assert trees.prune(repo) == ()
    dirty = tmp_path / "dirty"
    created = trees.create(_request(repo, dirty, "agent", "dirty"))
    trees.release(dirty)
    _ = (dirty / "extra.txt").write_text("x\n", encoding="utf-8")
    assert trees.close(repo, dirty) == CloseResult(removed=False, reason="dirty")
    assert dirty.is_dir()
    assert _branch_listed(repo, created.branch)

    ahead = tmp_path / "ahead"
    ahead_created = trees.create(_request(repo, ahead, "agent", "ahead"))
    trees.release(ahead)
    _ = (ahead / "note.txt").write_text("n\n", encoding="utf-8")
    _ = _git(ahead, "add", "note.txt")
    _commit(ahead, "note")
    assert trees.close(repo, ahead) == CloseResult(removed=False, reason="ahead")
    assert _branch_listed(repo, ahead_created.branch)

    clean = tmp_path / "clean"
    clean_created = trees.create(_request(repo, clean, "agent", "clean"))
    assert trees.close(repo, clean).reason == "locked"
    trees.release(clean)
    assert trees.close(repo, clean) == CloseResult(removed=True, reason="clean")
    assert not clean.exists()
    assert not _branch_listed(repo, clean_created.branch)

    stale = tmp_path / "stale"
    stale_created = trees.create(_request(repo, stale, "agent", "stale"))
    shutil.rmtree(stale)
    assert stale_created.branch in trees.prune(repo)
    listed = _git(repo, "worktree", "list", "--porcelain")
    assert str(stale.resolve()) not in listed
    assert trees.prune(repo) == ()


def test_worktrees_setup_timeout_failure_and_rejected_requests(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    setup = repo / ".aconsole" / "setup.sh"
    setup.parent.mkdir()
    _ = setup.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
    _ = _git(repo, "add", ".aconsole/setup.sh")
    _commit(repo, "setup")
    approved = _sha(setup)
    with pytest.raises(WorktreeError, match="timed out"):
        _ = _worktrees().create(
            _request(repo, tmp_path / "slow", "agent", "slow", setup_timeout_s=0.05, setup_sha256=approved),
        )
    _ = setup.write_text("#!/bin/sh\nexit 9\n", encoding="utf-8")
    _ = _git(repo, "add", ".aconsole/setup.sh")
    _commit(repo, "setup fails")
    approved = _sha(setup)
    with pytest.raises(WorktreeError, match="setup script failed") as failed:
        _ = _worktrees().create(_request(repo, tmp_path / "bad-setup", "agent", "bad", setup_sha256=approved))
    assert failed.value.code == 9
    with pytest.raises(WorktreeError, match="shell binary missing"):
        _ = Worktrees(_GIT, shell=tmp_path / "missing-sh").create(
            _request(repo, tmp_path / "no-sh", "agent", "nosh", setup_sha256=approved),
        )
    # Without an approved sha the script is skipped, so this create succeeds and stays locked.
    kept = _worktrees().create(_request(repo, tmp_path / "keep", "agent", "keep"))
    assert kept.setup_skipped is True
    trees = _worktrees()
    trees.release(kept.dest)
    kept_path = kept.dest.resolve()
    folder = repo / ".git" / "aconsole-worktrees"
    record = next(path for path in folder.iterdir() if str(kept_path) in path.read_text(encoding="utf-8"))
    _ = record.write_text("only\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="unreadable"):
        _ = trees.close(repo, kept_path)
    assert trees.prune(repo) == ()
    assert not record.exists()


def test_worktrees_rejects_bad_paths_names_and_a_missing_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    _ = (repo / ".worktreeinclude").write_text("../outside\n", encoding="utf-8")
    escape = _request(repo, tmp_path / "escape", "agent", "escape")
    assert _raised(ValueError, lambda: _worktrees().create(escape)) == "include pattern must stay inside the repository"
    (repo / ".worktreeinclude").unlink()
    bad_slug = _request(repo, tmp_path / "slug", "../x", "tab")
    assert _raised(ValueError, lambda: _worktrees().create(bad_slug)) == "slug must be a single token"
    bad_tab = _request(repo, tmp_path / "tab", "agent", "tab/1")
    assert _raised(ValueError, lambda: _worktrees().create(bad_tab)) == "tab id must be a single token"
    bad_base = _request(repo, tmp_path / "base", "agent", "base", base="-dirty")
    assert _raised(ValueError, lambda: _worktrees().create(bad_base)) == "base must be a git revision"
    zero = _request(repo, tmp_path / "zero", "agent", "zero", setup_timeout_s=0)
    assert _raised(ValueError, lambda: _worktrees().create(zero)) == "setup timeout must be positive"
    relative_repo = _request(Path("repo"), tmp_path / "rel", "agent", "rel")
    assert _raised(ValueError, lambda: _worktrees().create(relative_repo)) == "repository path must be absolute"
    relative_dest = _request(repo, Path("wt"), "agent", "reldest")
    assert _raised(ValueError, lambda: _worktrees().create(relative_dest)) == "worktree path must be absolute"
    missing_parent = _request(repo, tmp_path / "missing-parent" / "wt", "agent", "parent")
    assert _raised(ValueError, lambda: _worktrees().create(missing_parent)) == "worktree parent is missing"
    assert _raised(ValueError, lambda: _worktrees().create(_request(repo, repo, "agent", "inside"))) == (
        "worktree path already exists"
    )
    nested = repo / "nested"
    nested.mkdir()
    inside = _request(repo, nested / "wt", "agent", "nested")
    assert _raised(ValueError, lambda: _worktrees().create(inside)) == "worktree must be outside the repository"
    assert _raised(ValueError, lambda: Worktrees(Path("git"))) == "git binary path must be absolute"
    assert _raised(ValueError, lambda: Worktrees(_GIT, shell=Path("sh"))) == "shell path must be absolute"
    assert _raised(ValueError, lambda: Worktrees(_GIT, timeout_s=0)) == "timeout must be positive"
    missing = Worktrees(tmp_path / "missing-git")
    assert _raised(WorktreeError, lambda: missing.create(_request(repo, tmp_path / "no-git", "agent", "nogit"))) == (
        "git binary missing"
    )
    sleeper = tmp_path / "sleep-git"
    _ = sleeper.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
    sleeper.chmod(0o755)
    slow_git = Worktrees(sleeper, timeout_s=0.05)
    timed = _request(repo, tmp_path / "timeout", "agent", "time")
    assert _raised(WorktreeError, lambda: slow_git.create(timed)) == "git timed out"


def test_worktrees_ignored_files_keep_the_tree_until_discarded(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    _ = (repo / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    _ = _git(repo, "add", ".gitignore")
    _commit(repo, "ignore venv")
    trees = _worktrees()
    dest = tmp_path / "wt"
    created = trees.create(_request(repo, dest, "agent", "ign"))
    (dest / ".venv" / "bin").mkdir(parents=True)
    _ = (dest / ".venv" / "bin" / "python").write_text("x\n", encoding="utf-8")
    assert trees.state(repo, dest) == "ignored"
    trees.release(dest)
    kept = trees.close(repo, dest)
    assert kept == CloseResult(removed=False, reason="ignored")
    assert (dest / ".venv" / "bin" / "python").is_file()
    removed = trees.close(repo, dest, discard_ignored=True)
    assert removed == CloseResult(removed=True, reason="ignored")
    assert not dest.exists()
    assert not _branch_listed(repo, created.branch)


def test_worktrees_clean_tree_removes_its_branch_when_the_base_is_unmerged(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    _ = _git(repo, "checkout", "-b", "topic")
    _ = (repo / "topic.txt").write_text("t\n", encoding="utf-8")
    _ = _git(repo, "add", "topic.txt")
    _commit(repo, "topic")
    _ = _git(repo, "checkout", "main")
    trees = _worktrees()
    dest = tmp_path / "wt"
    created = trees.create(_request(repo, dest, "agent", "unmerged", base="topic"))
    trees.release(dest)
    assert trees.close(repo, dest) == CloseResult(removed=True, reason="clean")
    assert not dest.exists()
    assert not _branch_listed(repo, created.branch)


def test_distrobox_ini_diff_matches_the_fixture_and_apply_needs_a_token(tmp_path: Path) -> None:
    before = (_INI / "before.ini").read_text(encoding="utf-8")
    after = (_INI / "after.ini").read_text(encoding="utf-8")
    expected = (_INI / "expected.diff").read_text(encoding="utf-8")
    assert ini_diff(before, after) == expected
    ini_path = tmp_path / "distrobox.ini"
    _ = ini_path.write_text(before, encoding="utf-8")
    book = DistroboxIni(ini_path)
    assert book.diff(after) == expected
    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID") as missing:
        _ = book.apply(after, "")
    assert missing.value.code == 3007
    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID"):
        _ = book.preview(after, confirm="")
    preview = book.preview(after, confirm="shown-to-user")
    assert preview.diff == expected
    assert preview.confirm == "shown-to-user"
    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID"):
        _ = book.apply(after, "nope")
    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID"):
        _ = book.apply(before, "shown-to-user")
    backup = book.apply(after, "shown-to-user")
    assert backup.read_text(encoding="utf-8") == before
    assert ini_path.read_text(encoding="utf-8") == after
    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID"):
        _ = book.apply(after, "shown-to-user")
    with pytest.raises(IniError, match="ini is missing"):
        _ = DistroboxIni(tmp_path / "absent.ini").diff(after)
    binary = Path("/usr/bin/distrobox")
    assert assemble_argv(binary, ini_path, "agent-claude") == (
        str(binary),
        "assemble",
        "create",
        "--file",
        str(ini_path),
        "--name",
        "agent-claude",
        "--replace",
    )
    with pytest.raises(ValueError, match="absolute"):
        _ = assemble_argv(Path("distrobox"), ini_path, "agent-claude")
    with pytest.raises(ValueError, match="absolute"):
        _ = assemble_argv(binary, Path("distrobox.ini"), "agent-claude")
    with pytest.raises(ValueError, match="container name"):
        _ = assemble_argv(binary, ini_path, "../agent")


def test_worktrees_state_ignores_the_lock(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    trees = _worktrees()
    dest = tmp_path / "wt"
    created = trees.create(_request(repo, dest, "agent", "state1"))
    assert trees.state(repo, dest) == "clean"
    assert trees.locked(dest) is True
    assert dest.is_dir()
    _ = (dest / "extra.txt").write_text("x\n", encoding="utf-8")
    assert trees.state(repo, dest) == "dirty"
    (dest / "extra.txt").unlink()
    _ = (dest / "note.txt").write_text("n\n", encoding="utf-8")
    _ = _git(dest, "add", "note.txt")
    _commit(dest, "note")
    assert trees.state(repo, dest) == "ahead"
    assert created.branch.startswith("ac/")
    record = next((repo / ".git" / "aconsole-worktrees").iterdir())
    _ = record.write_text(f"{created.branch}\n{created.base}\n{tmp_path / 'elsewhere'}\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="unreadable"):
        _ = trees.state(repo, dest)
    with pytest.raises(ValueError, match="absolute"):
        _ = trees.state(Path("repo"), dest)


def _raised(exc: type[BaseException], call: Callable[[], object]) -> str:
    with pytest.raises(exc) as caught:
        _ = call()
    return str(caught.value)


def _worktrees() -> Worktrees:
    return Worktrees(_GIT)


def _request(
    repo: Path,
    dest: Path,
    slug: str,
    tab_id: str,
    *,
    base: str = "HEAD",
    setup_timeout_s: float = 30.0,
    setup_sha256: str | None = None,
) -> WorktreeRequest:
    return WorktreeRequest(
        repo=repo,
        dest=dest,
        slug=slug,
        tab_id=tab_id,
        base=base,
        setup_timeout_s=setup_timeout_s,
        setup_sha256=setup_sha256,
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _init(repo: Path) -> None:
    repo.mkdir()
    _git_ok("init", "-b", "main", str(repo))
    _ = (repo / "README").write_text("hi\n", encoding="utf-8")
    _ = _git(repo, "add", "README")
    _commit(repo, "init")


def _commit(repo: Path, message: str) -> None:
    _ = _git(repo, "-c", f"user.email={_EMAIL}", "-c", f"user.name={_NAME}", "commit", "-m", message)


def _branch_listed(repo: Path, branch: str) -> bool:
    return branch in _git(repo, "branch", "--list", branch).split()


def _git(repo: Path, *args: str) -> str:
    completed = _git_completed(("-C", str(repo), *args))
    if completed.returncode != 0:
        message = completed.stderr
        raise AssertionError(message)
    return completed.stdout


def _git_ok(*args: str) -> None:
    completed = _git_completed(args)
    if completed.returncode != 0:
        message = completed.stderr
        raise AssertionError(message)


def _git_completed(args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(_GIT), *args], check=False, capture_output=True, text=True)
