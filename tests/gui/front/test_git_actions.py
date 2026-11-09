"""The Git page's buttons and dialogs against the real core and a real repository."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

from tests.socket_paths import short_socket_path

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from aconsole.contracts.common import ProjectId, TabId  # noqa: E402
from aconsole.contracts.git import FactReviewView, FactsToReviewResult, GitStateResult, MergeProbeView  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.viewmodels.git_vm import RowFlags, actions_for, row_flags, tab_git_line  # noqa: E402
from aconsole.ui.views.review.git_pane import GitPane  # noqa: E402, TC001 -- annotations are read by GTK-free tests too
from tests.gui.front.test_git_pane import _commit, _git, _index, _on_pane, _row, _rows, _settled  # noqa: E402
lazy from aconsole.ui.viewmodels.tabs_vm import TabItem, TabsViewModel  # noqa: E402

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
    assert pane.git_actions.offered(key), f"{key} is not offered"
    pane.git_actions.buttons[key].emit("clicked")


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


def test_stage_commit_and_file_buttons_in_the_inspector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    (repo / "n.txt").write_text("n\n", encoding="utf-8")
    (repo / "m.txt").write_text("m\n", encoding="utf-8")
    (repo / "a.txt").write_text("edited\n", encoding="utf-8")

    async def check(pane: GitPane, toasts: list[str], _window: Adw.ApplicationWindow) -> None:
        path = str(repo.resolve())
        actions = pane.git_actions
        await _select(pane, path)
        assert not actions.commit_button.get_sensitive()

        # One file at a time, from its own row.
        await _settled(lambda: ("git.file_stage", "n.txt") in actions.file_actions)
        actions.file_actions["git.file_stage", "n.txt"].emit("clicked")
        await _settled(lambda: "A  n.txt" in _git(repo, "status", "--porcelain"))
        assert "?? m.txt" in _git(repo, "status", "--porcelain")

        # Discarding asks first, says what happens, and keeps a way back.
        await _settled(lambda: ("git.file_discard", "a.txt") in actions.file_actions)
        actions.file_actions["git.file_discard", "a.txt"].emit("clicked")
        await _settled(lambda: actions.dialog is not None)
        assert (repo / "a.txt").read_text(encoding="utf-8") == "edited\n"
        _answer(pane)
        await _settled(lambda: (repo / "a.txt").read_text(encoding="utf-8") == "1\n")
        assert _git(repo, "for-each-ref", "refs/aconsole/safety")

        await _settled(lambda: pane.state is not None and any(row.staged == 1 for row in pane.state.worktrees))
        await _select(pane, path)
        actions.message_view.get_buffer().set_text("from the inspector")
        await _settled(actions.commit_button.get_sensitive)
        actions.commit_button.emit("clicked")
        await _settled(lambda: _git(repo, "log", "-1", "--format=%s") == "from the inspector")
        await _settled(lambda: bool(toasts))
        assert _git(repo, "show", "--name-only", "--format=", "HEAD") == "n.txt"
        await _settled(lambda: actions.message_view.get_buffer().get_char_count() == 0)

    _on_pane(tmp_path, monkeypatch, repo, check)


def test_merge_removes_the_worktree_unless_told_not_to(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    tree = tmp_path / "wt"
    _ = _git(repo, "worktree", "add", "-q", "-b", "ac/feature-1", str(tree))
    _ = _commit(tree, "b.txt", "x\n", "agent work")
    keep = tmp_path / "keep"
    _ = _git(repo, "worktree", "add", "-q", "-b", "ac/feature-2", str(keep))
    _ = _commit(keep, "k.txt", "k\n", "kept work")
    _ = _commit(repo, "c.txt", "c\n", "base moved")

    async def check(pane: GitPane, toasts: list[str], _window: Adw.ApplicationWindow) -> None:
        actions = pane.git_actions
        await _select(pane, "agent work")
        _press(pane, "merge")
        await _settled(lambda: actions.mode_choice is not None)
        assert actions.cleanup_choice is not None
        assert actions.cleanup_choice.get_active(), "on by default"
        _answer(pane)
        await _settled((repo / "b.txt").exists)
        await _settled(lambda: not tree.exists())
        assert _git(repo, "log", "-1", "--format=%s").startswith("Merge ac/feature-1 into develop")
        await _settled(lambda: _git(repo, "branch", "--list", "ac/feature-1") == "")

        await _settled(lambda: not pane.git_actions.dialog or pane.state is not None)
        await pane.refresh()
        await _select(pane, "kept work")
        actions.mode_choice = None
        _press(pane, "merge")
        await _settled(lambda: actions.mode_choice is not None)
        assert actions.cleanup_choice is not None
        actions.cleanup_choice.set_active(False)
        _answer(pane)
        await _settled((repo / "k.txt").exists)
        await pane.refresh()
        assert keep.exists()
        await _select(pane, "kept work")
        await _settled(lambda: actions.offered("cleanup"))
        count = len(toasts)
        _press(pane, "cleanup")
        _answer(pane)
        await _settled(lambda: not keep.exists() and len(toasts) > count)

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
        assert pane.git_actions.offered("continue")
        assert not pane.git_actions.offered("merge")
        _press(pane, "abort")
        _answer(pane)
        try:
            await _settled(
                lambda: _git(tree, "status", "--porcelain") == "" and _git(tree, "rev-parse", "HEAD") == before
            )
        except AssertionError as error:
            error.add_note(f"Abort messages: {toasts!r}; selected row: {pane.selected_row()!r}")
            error.add_note(
                f"Expected HEAD: {before}; actual HEAD: {_git(tree, 'rev-parse', 'HEAD')}; "
                f"status: {_git(tree, '--no-optional-locks', 'status', '--porcelain')!r}"
            )
            raise

        # The user looks, another terminal stages something, then the user commits what they saw.
        (repo / "x.txt").write_text("x\n", encoding="utf-8")
        _ = _git(repo, "add", "x.txt")
        await pane.refresh()
        await _select(pane, str(repo.resolve()))
        (repo / "y.txt").write_text("y\n", encoding="utf-8")
        _ = _git(repo, "add", "y.txt")
        count = len(toasts)
        pane.git_actions.message_view.get_buffer().set_text("stale")
        await _settled(pane.git_actions.commit_button.get_sensitive)
        pane.git_actions.commit_button.emit("clicked")
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
    assert actions_for(clean, None, base_branch="develop", has_recipient=False, has_file=False) == ("merge", "cleanup")
    behind = _row(is_main=False, branch="ac/x", behind=2)
    assert actions_for(behind, None, base_branch="develop", has_recipient=False, has_file=False) == (
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
    assert actions_for(main, None, base_branch="develop", has_recipient=False, has_file=False) == ("stage", "unstage")
    assert row_flags(main, "develop").can_commit
    assert row_flags(stopped, "develop") == RowFlags(
        can_commit=False, can_merge=False, is_merging=True, has_conflicts=True
    )


def test_a_tab_shows_its_branch_node_and_size() -> None:
    probe = MergeProbeView(status="clean", source_oid="a", target_oid="b", insertions=84, deletions=12)
    row = _row(is_main=False, branch="ac/n03", merge=probe, plan_node="N-03")
    assert tab_git_line(row) == "ac/n03 · N-03  +84 \u221212"


def test_the_sidebar_item_follows_git_state(tmp_path: Path) -> None:
    tabs = TabsViewModel(CoreClient(str(short_socket_path(tmp_path / "unused.sock"))))
    item = TabItem("t1", "one")
    item.set_property("project-id", "p1")
    tabs.store.append(item)
    other = TabItem("t2", "other project")
    other.set_property("project-id", "p2")
    tabs.store.append(other)
    conflict = MergeProbeView(status="conflict", source_oid="a", target_oid="b", conflicts=("x",))
    row = _row(is_main=False, branch="ac/x", tab_id=TabId("t1"), merge=conflict)
    state = GitStateResult(
        project_id=ProjectId("p1"),
        revision="r",
        root="/r",
        empty=False,
        base_branch="develop",
        base_oid="b",
        worktrees=(row,),
        branches=(),
        operations=(),
    )
    tabs.apply_git(state)
    assert str(item.git_line) == "ac/x"
    assert bool(item.git_warning)
    assert str(other.git_line) == ""


def test_the_facts_banner_shows_what_the_core_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)

    async def check(pane: GitPane, _toasts: list[str], _window: Adw.ApplicationWindow) -> None:
        actions = pane.git_actions
        await _settled(lambda: pane.state is not None)
        assert not actions.banner.get_revealed(), "no facts, no banner"
        fact = FactReviewView(id="kb-1", title="about a", verified_commit="c" * 40, changed_paths=("a.txt",))

        async def found() -> FactsToReviewResult:
            return FactsToReviewResult(head_oid="h", facts=(fact,), note_path=str(tmp_path / "note.md"))

        pane.viewmodel.facts = found  # pyright: ignore[reportAttributeAccessIssue]
        _ = _commit(repo, "a.txt", "2\n", "moves a")
        await pane.refresh()
        await _settled(actions.banner.get_revealed)
        assert "1" in actions.banner.get_title()
        actions.banner.emit("button-clicked")
        assert actions.dialog is not None
        assert "about a: a.txt" in actions.dialog.get_body()

    _on_pane(tmp_path, monkeypatch, repo, check)
