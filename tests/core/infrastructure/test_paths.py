"""OsPathResolver follows symbolic links on the real filesystem."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aconsole.core.infrastructure.paths import OsPathResolver

if TYPE_CHECKING:
    from pathlib import Path


def test_symlink_inside_the_worktree_resolves_outside_it(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    outside = tmp_path / "outside.txt"
    _ = outside.write_text("secret", encoding="utf-8")
    (worktree / "link").symlink_to(outside)
    real = OsPathResolver().resolve("link", str(worktree))
    assert real == str(outside.resolve())
    assert not real.startswith(str(worktree.resolve()) + "/")


def test_plain_file_inside_the_worktree_resolves_inside_it(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    (worktree / "sub").mkdir(parents=True)
    _ = (worktree / "sub" / "README.md").write_text("hi", encoding="utf-8")
    real = OsPathResolver().resolve("sub/README.md", str(worktree))
    assert real == str((worktree / "sub" / "README.md").resolve())
    assert real.startswith(str(worktree.resolve()) + "/")
