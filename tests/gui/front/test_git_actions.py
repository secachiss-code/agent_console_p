"""The Git page's buttons and dialogs against the real core and a real repository."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from aconsole.ui.viewmodels.git_vm import actions_for  # noqa: E402
from tests.gui.front.test_git_pane import _commit, _git, _index, _on_pane, _row, _rows, _settled  # noqa: E402
lazy from aconsole.ui.views.review.git_pane import GitPane  # noqa: E402

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from gi.repository import Adw


async def _select(pane: GitPane, wanted: str) -> None:
    """Select the row whose worktree path or commit subject is `wanted`."""

    def matches(item: object) -> bool:
        row = item.row  # pyright: ignore[reportAttributeAccessIssue]
        if row.worktree is not None:
            return row.worktree.path == wanted
        return row.node is not None and row.node.subject == wanted

    await _settled(lambda: any(matches(item) for item in _rows(pane)))
    pane.selection.set_selected(_index(pane, matches))
    await _settled(lambda: pane.selected_row() is not None)


def _press(pane: GitPane, key: str) -> None:
    button = pane.git_actions.buttons[key]
    assert button.get_visible(), f"{key} is not offered"
    button.emit("clicked")


def _answer(pane: GitPane) -> None:
    dialog = pane.git_actions.dialog
    assert dialog is not None
    dialog.emit("response", "go")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _ = _git(repo, "init", "-q", "-b", "develop")
    _ = _commit(repo, "a.txt", "1\n", "first")
    return repo


def test_stage_and_commit_through_the_dialog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    (repo / "n.txt").write_text("n\n", encoding="utf-8")

    async def check(pane: GitPane, toasts: list[str], _window: Adw.ApplicationWindow) -> None:
        path = str(repo.resolve())
        await _select(pane, path)
        assert not pane.git_actions.buttons["commit"].get_visible()
        _press(pane, "stage")
        await _settled(pane.git_actions.buttons["commit"].get_visible)
        _press(pane, "commit")
        view = pane.git_actions.message_view
        assert view is not None
        view.get_buffer().set_text("from the dialog")
        _answer(pane)
        await _settled(lambda: _git(repo, "log", "-1", "--format=%s") == "from the dialog")
        await _settled(lambda: bool(toasts))
        assert (repo / "n.txt").exists()
        assert _git(repo, "status", "--porcelain") == ""

    _on_pane(tmp_path, monkeypatch, repo, check)


def test_merge_then_remove_the_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    tree = tmp_path / "wt"
    _ = _git(repo, "worktree", "add", "-q", "-b", "ac/feature-1", str(tree))
    _ = _commit(tree, "b.txt", "x\n", "agent work")
    _ = _commit(repo, "c.txt", "c\n", "base moved")

    async def check(pane: GitPane, toasts: list[str], _window: Adw.ApplicationWindow) -> None:
        await _select(pane, "agent work")
        _press(pane, "merge")
        await _settled(lambda: pane.git_actions.mode_choice is not None)
        _answer(pane)
        await _settled((repo / "b.txt").exists)
        await _settled(lambda: bool(toasts))
        assert _git(repo, "log", "-1", "--format=%s") == "Merge ac/feature-1 into develop"
        await _select(pane, "agent work")
        await _settled(pane.git_actions.buttons["cleanup"].get_visible)
        _press(pane, "cleanup")
        _answer(pane)
        await _settled(lambda: not tree.exists())
        await _settled(lambda: _git(repo, "branch", "--list", "ac/feature-1") == "")

    _on_pane(tmp_path, monkeypatch, repo, check)


def test_a_conflicted_rebase_offers_abort_and_a_stale_commit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    tree = tmp_path / "wt"
    _ = _git(repo, "worktree", "add", "-q", "-b", "ac/feature-1", str(tree))
    _ = _commit(tree, "a.txt", "work\n", "work side")
    _ = _commit(repo, "a.txt", "base\n", "base side")

    async def check(pane: GitPane, toasts: list[str], _window: Adw.ApplicationWindow) -> None:
        await _select(pane, "work side")
        before = _git(tree, "rev-parse", "HEAD")
        _press(pane, "rebase")
        _answer(pane)
        await _settled(lambda: (tree / ".git").exists() and bool(toasts))
        assert "конфликт" in toasts[-1].lower() or "conflict" in toasts[-1].lower()
        await _select(pane, str(tree.resolve()))
        await _settled(pane.git_actions.buttons["abort"].get_visible)
        assert pane.git_actions.buttons["continue"].get_visible()
        assert not pane.git_actions.buttons["merge"].get_visible()
        _press(pane, "abort")
        _answer(pane)
        await _settled(lambda: _git(tree, "status", "--porcelain") == "" and _git(tree, "rev-parse", "HEAD") == before)

        # The user looks, another terminal stages something, then the user commits what they saw.
        (repo / "x.txt").write_text("x\n", encoding="utf-8")
        _ = _git(repo, "add", "x.txt")
        await pane.refresh()
        await _select(pane, str(repo.resolve()))
        await _settled(pane.git_actions.buttons["commit"].get_visible)
        (repo / "y.txt").write_text("y\n", encoding="utf-8")
        _ = _git(repo, "add", "y.txt")
        count = len(toasts)
        _press(pane, "commit")
        view = pane.git_actions.message_view
        assert view is not None
        view.get_buffer().set_text("stale")
        _answer(pane)
        await _settled(lambda: len(toasts) > count)
        assert _git(repo, "log", "-1", "--format=%s") != "stale"

    _on_pane(tmp_path, monkeypatch, repo, check)


def test_restore_a_file_from_a_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    _ = _commit(repo, "a.txt", "2\n", "second")

    async def check(pane: GitPane, _toasts: list[str], _window: Adw.ApplicationWindow) -> None:
        await _select(pane, "second")
        await _settled(lambda: pane.selected_path == "a.txt")
        await _settled(pane.git_actions.buttons["restore"].get_visible)
        (repo / "a.txt").write_text("edited\n", encoding="utf-8")
        _press(pane, "restore")
        await _settled(lambda: pane.git_actions.dialog is not None)
        _answer(pane)
        await _settled(lambda: (repo / "a.txt").read_text(encoding="utf-8") == "2\n")
        assert _git(repo, "for-each-ref", "refs/aconsole/safety")

    _on_pane(tmp_path, monkeypatch, repo, check)


def test_what_a_row_offers() -> None:
    clean = _row(is_main=False, branch="ac/x")
    assert actions_for(clean, None, base_branch="develop", has_recipient=False, has_file=False) == (
        "merge",
        "rebase",
        "cleanup",
    )
    busy = _row(is_main=False, branch="ac/x", busy=True, staged=2)
    assert actions_for(busy, None, base_branch="develop", has_recipient=True, has_file=False) == ("send",)
    stopped = _row(is_main=False, branch="ac/x", in_progress="rebase", conflicted=1)
    assert actions_for(stopped, None, base_branch="develop", has_recipient=False, has_file=False) == (
        "continue",
        "abort",
    )
    main = _row(is_main=True, branch="develop", staged=1, unstaged=1)
    assert actions_for(main, None, base_branch="develop", has_recipient=False, has_file=False) == (
        "stage",
        "unstage",
        "commit",
    )
