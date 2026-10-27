"""Branch names, dirtiness, and close decisions."""

import pytest

from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.workspace import (
    CloseChoice,
    Dirt,
    WorkspaceMode,
    branch_name,
    claim_branch,
    decide_close,
    dirty_summary,
    is_dirty,
    path_segment,
    slug,
    worktree_path,
)


def test_branch_names_are_slugs_under_ac() -> None:
    assert slug("My Feature!") == "my-feature"
    assert slug("  ") == "tab"
    assert slug("a" * 50) == "a" * 40
    assert branch_name("My Feature!", "01JB2Z00000000000000000001") == "ac/my-feature-01jb2z00000000000000000001"
    assert branch_name("../../../etc", "tab/1") == "ac/etc-tab-1"
    assert path_segment("..") == "item"
    named = branch_name("Hello/World", "id 1")
    assert named.startswith("ac/")
    assert named.count("/") == 1


def test_taken_branch_gets_a_numeric_suffix() -> None:
    taken = frozenset({"ac/demo-tab1"})
    assert branch_name("demo", "tab1", taken) == "ac/demo-tab1-2"
    with pytest.raises(DomainError, match="BRANCH_ALREADY_CHECKED_OUT"):
        claim_branch("ac/demo-tab1", taken)


def test_worktree_path_stays_under_the_root() -> None:
    assert worktree_path("/home/sas/.local/share/aconsole/worktrees", "repo", "tab 1") == (
        "/home/sas/.local/share/aconsole/worktrees/repo/tab-1"
    )
    assert ".." not in worktree_path("/var/worktrees", "../repo", "..")


def test_dirty_summary_counts_each_kind() -> None:
    assert is_dirty(Dirt()) is False
    assert dirty_summary(Dirt()) is None
    facts = Dirt(modified=2, staged=1, untracked=3)
    assert is_dirty(facts) is True
    assert dirty_summary(facts) == "2 изменено, 1 в индексе, 3 новых"


def test_dirty_close_asks_until_the_user_chooses() -> None:
    facts = Dirt(modified=1)
    asked = decide_close(facts, None, mode=WorkspaceMode.NEW_WORKTREE, branch="ac/demo-tab")
    assert asked.closed is False
    assert asked.needs_decision is True
    assert asked.remove_worktree is False
    kept = decide_close(facts, CloseChoice.KEEP, mode=WorkspaceMode.NEW_WORKTREE, branch="ac/demo-tab")
    assert kept.closed is True
    assert kept.remove_worktree is False
    removed = decide_close(facts, CloseChoice.REMOVE, mode=WorkspaceMode.NEW_WORKTREE, branch="ac/demo-tab")
    assert removed.remove_worktree is True
    assert removed.delete_branch is True
    assert removed.force is False


def test_clean_worktree_we_created_is_removed_without_force() -> None:
    facts = Dirt()
    closed = decide_close(facts, None, mode=WorkspaceMode.NEW_WORKTREE, branch="ac/demo-tab")
    assert closed.closed is True
    assert closed.needs_decision is False
    assert closed.remove_worktree is True
    assert closed.delete_branch is True
    assert closed.force is False
    foreign = decide_close(facts, CloseChoice.REMOVE, mode=WorkspaceMode.NEW_WORKTREE, branch="main")
    assert foreign.remove_worktree is True
    assert foreign.delete_branch is False


def test_main_checkout_and_existing_worktree_are_not_deleted() -> None:
    facts = Dirt(untracked=1)
    main = decide_close(facts, CloseChoice.REMOVE, mode=WorkspaceMode.MAIN_CHECKOUT, branch="main")
    existing = decide_close(facts, CloseChoice.REMOVE, mode=WorkspaceMode.EXISTING_WORKTREE, branch="ac/demo-tab")
    assert main.remove_worktree is False
    assert main.delete_branch is False
    assert existing.remove_worktree is False
    assert existing.delete_branch is False
    assert main.force is False
    assert existing.force is False
