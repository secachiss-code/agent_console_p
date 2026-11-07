"""Checkpoint refs restore a worktree and leave ignored files alone."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from aconsole.core.application.services.checkpoints import CheckpointRestore, CheckpointService
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.tab_status import TabStatus
from aconsole.core.infrastructure.git.checkpoints import GitCheckpoints

if TYPE_CHECKING:
    from pathlib import Path

_GIT = "/usr/bin/git"
_AT = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def test_checkpoints_git_restores_untracked_deleted_and_renamed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _commit_base(repo)
    (repo / "b.txt").unlink()
    (repo / "c.txt").unlink()
    (repo / "sub").mkdir()
    (repo / "sub" / "d.txt").write_text("renamed\n", encoding="utf-8")
    (repo / "new.txt").write_text("fresh\n", encoding="utf-8")
    (repo / "secret.txt").write_text("ignored-v1\n", encoding="utf-8")
    service = CheckpointService(GitCheckpoints())
    made = service.on_user_prompt(repo, "tab-1", "hello there\nmore", _AT)
    assert made.label == "hello there"
    assert made.ref == "refs/aconsole/checkpoints/tab-1/1"
    assert service.list("tab-1") == (made,)
    (repo / "a.txt").write_text("later\n", encoding="utf-8")
    (repo / "b.txt").write_text("came-back\n", encoding="utf-8")
    (repo / "c.txt").write_text("back\n", encoding="utf-8")
    (repo / "sub" / "d.txt").unlink()
    (repo / "new.txt").unlink()
    (repo / "secret.txt").write_text("ignored-v2\n", encoding="utf-8")
    with pytest.raises(DomainError) as caught:
        _ = service.restore(
            CheckpointRestore(
                repo=repo, tab_id="tab-1", checkpoint_id=made.id, confirm="", status=TabStatus.IDLE, at=_AT
            )
        )
    assert caught.value.name == "CONFIRM_TOKEN_INVALID"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "later\n"
    preview = service.preview("tab-1", made.id, now=_AT)
    result = service.restore(
        CheckpointRestore(
            repo=repo,
            tab_id="tab-1",
            checkpoint_id=made.id,
            confirm=preview.confirm_token,
            status=TabStatus.IDLE,
            at=_AT,
        )
    )
    assert result.restored is True
    assert (repo / "a.txt").read_text(encoding="utf-8") == "one\n"
    assert not (repo / "b.txt").exists()
    assert not (repo / "c.txt").exists()
    assert (repo / "sub" / "d.txt").read_text(encoding="utf-8") == "renamed\n"
    assert (repo / "new.txt").read_text(encoding="utf-8") == "fresh\n"
    assert (repo / "secret.txt").read_text(encoding="utf-8") == "ignored-v2\n"
    safety = next(item for item in service.list("tab-1") if item.id == result.safety_checkpoint)
    assert _show(repo, safety.ref, "a.txt") == "later\n"


def _commit_base(repo: Path) -> None:
    repo.mkdir()
    _git(repo, ("init",))
    _git(repo, ("config", "user.email", "t@example.com"))
    _git(repo, ("config", "user.name", "t"))
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    (repo / "b.txt").write_text("gone\n", encoding="utf-8")
    (repo / "c.txt").write_text("old\n", encoding="utf-8")
    (repo / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
    _git(repo, ("add", "a.txt", "b.txt", "c.txt", ".gitignore"))
    _git(repo, ("commit", "-m", "init"))


def _show(repo: Path, ref: str, path: str) -> str:
    return _git(repo, ("show", f"{ref}:{path}"))


def _git(repo: Path, args: tuple[str, ...]) -> str:
    completed = subprocess.run(
        (_GIT, "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def test_a_checkpoint_needs_no_user_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _commit_base(repo)
    _ = _git(repo, ("config", "--unset", "user.name"))
    _ = _git(repo, ("config", "--unset", "user.email"))
    empty = tmp_path / "empty-gitconfig"
    _ = empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    made = CheckpointService(GitCheckpoints()).on_user_prompt(repo, "tab-1", "work", _AT)
    assert _show(repo, made.ref, "a.txt") == "changed\n"
    assert _git(repo, ("log", "-1", "--format=%an", made.ref)).strip() == "AConsole"


@pytest.mark.parametrize("external", [False, True])
def test_restore_removes_dangling_symlink_and_safety_restores_it(tmp_path: Path, *, external: bool) -> None:
    repo = tmp_path / "repo"
    _commit_base(repo)
    service = CheckpointService(GitCheckpoints())
    made = service.on_user_prompt(repo, "tab-1", "before link", _AT)
    head = _git(repo, ("rev-parse", "HEAD"))
    index = (repo / ".git/index").read_bytes()
    outside = tmp_path / "outside"
    outside.mkdir()
    external_file = outside / "sentinel.txt"
    external_file.write_text("preserve\n", encoding="utf-8")
    target = str(outside / "missing") if external else "missing"
    link = repo / "extra-link"
    link.symlink_to(target)
    ignored = repo / "secret.txt"
    ignored.symlink_to(target)
    assert link.is_symlink()
    assert not link.exists()
    preview = service.preview("tab-1", made.id, now=_AT)
    result = service.restore(
        CheckpointRestore(
            repo=repo, tab_id="tab-1", checkpoint_id=made.id,
            confirm=preview.confirm_token, status=TabStatus.IDLE, at=_AT,
        )
    )
    assert result.restored
    assert not link.is_symlink()
    assert ignored.is_symlink()
    safety = next(item for item in service.list("tab-1") if item.id == result.safety_checkpoint)
    preview = service.preview("tab-1", safety.id, now=_AT)
    restored = service.restore(
        CheckpointRestore(
            repo=repo, tab_id="tab-1", checkpoint_id=safety.id,
            confirm=preview.confirm_token, status=TabStatus.IDLE, at=_AT,
        )
    )
    assert restored.restored
    assert link.is_symlink()
    assert not link.exists()
    assert str(link.readlink()) == target
    assert external_file.read_text(encoding="utf-8") == "preserve\n"
    assert not (outside / "missing").exists()
    assert _git(repo, ("rev-parse", "HEAD")) == head
    assert (repo / ".git/index").read_bytes() == index
