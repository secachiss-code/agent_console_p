"""What the Git page shows, against what Git itself answers. Each expected value comes from plain git commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from aconsole.ui.views.review.messages import _ as translate  # noqa: E402
from tests.gui.front.test_git_pane import _commit, _git, _labels, _on_pane, _rows, _settled  # noqa: E402

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from gi.repository import Adw

    from aconsole.contracts.git import WorktreeGitState
    from aconsole.ui.views.review.git_pane import GitPane

_BASE = "develop"
_CLEAN = "ac/clean"
_CONFLICT = "ac/conflict"
_DIRTY = "ac/dirty"


def _repository(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _ = _git(repo, "init", "-q", "-b", _BASE)
    _ = _commit(repo, "shared.txt", "one\ntwo\n", "first")
    trees = {name: tmp_path / name.replace("/", "-") for name in (_CLEAN, _CONFLICT, _DIRTY)}
    for name, tree in trees.items():
        _ = _git(repo, "worktree", "add", "-q", "-b", name, str(tree))
    _ = _commit(trees[_CLEAN], "new.txt", "a\nb\nc\n", "add three lines")
    _ = _commit(trees[_CLEAN], "new.txt", "a\nc\n", "drop one line")
    _ = _commit(trees[_CONFLICT], "shared.txt", "theirs\ntwo\n", "change the first line")
    _ = _commit(repo, "shared.txt", "ours\ntwo\n", "base moved")
    _ = _commit(trees[_DIRTY], "kept.txt", "1\n2\n", "keep two lines")
    _ = (trees[_DIRTY] / "kept.txt").write_text("1\nchanged\n3\n", encoding="utf-8")
    _ = (trees[_DIRTY] / "staged.txt").write_text("in the index\n", encoding="utf-8")
    _ = _git(trees[_DIRTY], "add", "--", "staged.txt")
    _ = (trees[_DIRTY] / "untracked.txt").write_text("nobody added this\n", encoding="utf-8")
    return repo, trees


def _lines_changed(repo: Path, branch: str) -> tuple[int, int]:
    start = _git(repo, "merge-base", _BASE, branch)
    insertions = deletions = 0
    for line in _git(repo, "diff", "--numstat", start, branch).splitlines():
        added, removed, _path = line.split("\t")
        insertions += int(added)
        deletions += int(removed)
    return insertions, deletions


def _status_counts(tree: Path) -> tuple[int, int, int]:
    staged = _git(tree, "diff", "--cached", "--name-only").splitlines()
    unstaged = _git(tree, "diff", "--name-only").splitlines()
    untracked = _git(tree, "ls-files", "--others", "--exclude-standard").splitlines()
    return len(staged), len(unstaged), len(untracked)


def _check_row(repo: Path, row: WorktreeGitState) -> None:
    branch = row.branch
    assert branch is not None
    ahead = _git(repo, "rev-list", "--count", f"{_BASE}..{branch}")
    behind = _git(repo, "rev-list", "--count", f"{branch}..{_BASE}")
    assert (row.ahead, row.behind) == (int(ahead), int(behind)), branch
    assert row.head_oid == _git(repo, "rev-parse", branch), branch


def test_the_page_says_what_git_says(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, trees = _repository(tmp_path)

    async def check(pane: GitPane, _toasts: list[str], _window: Adw.ApplicationWindow) -> None:
        await _settled(lambda: pane.state is not None and pane.store.get_n_items() > 0)
        state = pane.state
        assert state is not None
        by_branch = {row.branch: row for row in state.worktrees if not row.is_main}
        assert set(by_branch) == set(trees)
        for row in by_branch.values():
            _check_row(repo, row)

        # The numbers on the rows are the ones `git diff` gives, which +0 -0 once was not.
        clean = _lines_changed(repo, _CLEAN)
        assert clean == (2, 0)
        expected = translate("git.badge_ready").format(insertions=clean[0], deletions=clean[1])
        await _settled(lambda: expected in _labels(pane.list_view))
        assert (by_branch[_CLEAN].merge.insertions, by_branch[_CLEAN].merge.deletions) == clean

        # A conflict only if Git cannot merge: `merge-tree` exits 1 and names the file.
        assert by_branch[_CONFLICT].merge.status == "conflict"
        assert by_branch[_CONFLICT].merge.conflicts == ("shared.txt",)
        assert by_branch[_DIRTY].merge.status == "clean"

        dirty = by_branch[_DIRTY]
        assert (dirty.staged, dirty.unstaged, dirty.untracked) == _status_counts(trees[_DIRTY])
        assert (dirty.staged, dirty.unstaged, dirty.untracked) == (1, 1, 1)

        # Every commit Git has on a branch has a row, and no row has a commit it does not.
        subjects = set(_git(repo, "log", "--branches", "--format=%s").splitlines())
        shown = {item.row.node.subject for item in _rows(pane) if item.row.node is not None}
        assert shown == subjects

        # The file list agrees with the worktree's own diff.
        at = next(
            index for index, item in enumerate(_rows(pane))
            if item.row.worktree is not None and item.row.worktree.branch == _DIRTY
        )
        pane.selection.set_selected(at)
        await _settled(lambda: "kept.txt" in _labels(pane.file_list))
        labels = _labels(pane.file_list)
        added, removed, _path = _git(trees[_DIRTY], "diff", "--numstat", "--", "kept.txt").split("\t")
        assert (added, removed) == ("2", "1")
        assert f"+{added} −{removed}" in labels  # noqa: RUF001 -- the minus sign the page uses
        assert "untracked.txt" in labels
        assert "staged.txt" not in labels, "the default mode shows what is not in the index"

    _on_pane(tmp_path, monkeypatch, repo, check)
