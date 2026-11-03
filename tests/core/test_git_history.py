"""The machine-readable Git layer against real repositories."""

from __future__ import annotations

from pathlib import Path

import pytest

from aconsole.core.application.ports.git_repo import DiffSpec, GitCommandError
from aconsole.core.infrastructure.git.history import GitHistory
from aconsole.core.infrastructure.git.runner import GitRunner
from tests.core.git_helpers import add_worktree, commit, git, new_repo


def test_empty_repository_has_no_head(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    reader = GitHistory()
    rows = reader.worktrees(repo)
    assert rows[0].head is None
    assert rows[0].is_main
    assert reader.refs(repo) == ()
    assert reader.log(repo, (), 0, 10) == ()
    assert reader.status(repo).staged == 0


def test_worktrees_detached_and_kept_branches(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    first = commit(repo, "a.txt", "1\n", "one")
    wide = add_worktree(repo, tmp_path / "wt one", "ac/one")
    _ = git(wide, "checkout", "-q", "--detach")
    rows = GitHistory().worktrees(repo)
    assert [row.branch for row in rows] == ["trunk", None]
    assert rows[1].detached
    assert rows[1].path == str(wide.resolve())
    assert rows[1].head == first


def test_status_counts_and_digests(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "a.txt", "1\n", "one")
    _ = commit(repo, "b.txt", "1\n", "two")
    reader = GitHistory()
    clean = reader.status(repo)
    assert (clean.staged, clean.unstaged, clean.untracked, clean.conflicted) == (0, 0, 0, 0)

    (repo / "a.txt").write_text("2\n", encoding="utf-8")
    edited = reader.status(repo)
    assert edited.unstaged == 1
    assert edited.index_digest == clean.index_digest
    assert edited.digest != clean.digest

    _ = git(repo, "add", "a.txt")
    _ = git(repo, "mv", "b.txt", "c d\n.txt")
    (repo / "new тест.txt").write_text("x\n", encoding="utf-8")
    moved = reader.status(repo)
    assert moved.staged == 2
    assert moved.untracked == 1
    assert moved.index_digest != clean.index_digest


def test_lock_file_is_not_dirt(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "a.txt", "1\n", "one")
    (repo / ".aconsole").mkdir()
    (repo / ".aconsole" / "lock").write_text("tab\n", encoding="utf-8")
    assert GitHistory().status(repo).untracked == 0


def test_merge_in_progress_is_seen_without_a_process(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "a.txt", "1\n", "one")
    wide = add_worktree(repo, tmp_path / "wt", "ac/one")
    _ = commit(wide, "a.txt", "theirs\n", "theirs")
    _ = commit(repo, "a.txt", "ours\n", "ours")
    with pytest.raises(Exception, match="returned non-zero"):
        _ = git(repo, "merge", "ac/one")
    state = GitHistory().status(repo)
    assert state.in_progress == "merge"
    assert state.conflicted == 1
    assert GitHistory().status(wide).in_progress is None


def test_merge_probe_clean_and_conflict(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    _ = commit(repo, "a.txt", "1\n", "one")
    wide = add_worktree(repo, tmp_path / "wt", "ac/one")
    other = add_worktree(repo, tmp_path / "wt2", "ac/two")
    clean_side = commit(wide, "b.txt", "x\n", "b")
    bad_side = commit(other, "a.txt", "theirs\n", "t")
    target = commit(repo, "a.txt", "ours\n", "o")
    before = git(repo, "status", "--porcelain")
    reader = GitHistory()

    clean = reader.merge_probe(repo, target, clean_side)
    assert clean.clean
    assert clean.tree is not None
    conflict = reader.merge_probe(repo, target, bad_side)
    assert not conflict.clean
    assert conflict.conflicts == ("a.txt",)
    assert git(repo, "status", "--porcelain") == before
    assert reader.ahead_behind(repo, clean_side, target) == (1, 1)
    assert reader.is_ancestor(repo, clean_side, target) is False


def test_log_pages_and_odd_subjects(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    oids = [commit(repo, "a.txt", f"{n}\n", f"тема {n}: <b>&") for n in range(5)]
    reader = GitHistory()
    first = reader.log(repo, (oids[-1],), 0, 2)
    second = reader.log(repo, (oids[-1],), 2, 2)
    assert [row.oid for row in first] == [oids[4], oids[3]]
    assert [row.oid for row in second] == [oids[2], oids[1]]
    assert first[0].subject == "тема 4: <b>&"
    assert first[0].parents == (oids[3],)
    assert reader.log(repo, (oids[-1],), 4, 2)[0].parents == ()


def test_diff_sources_keep_index_worktree_and_new_files_apart(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    head = commit(repo, "a.txt", "1\n2\n", "one")
    (repo / "a.txt").write_text("1\n2\n3\n", encoding="utf-8")
    _ = git(repo, "add", "a.txt")
    (repo / "b.txt").write_text("new\n", encoding="utf-8")
    (repo / "a.txt").write_text("1\n2\n3\n4\n", encoding="utf-8")
    reader = GitHistory()

    staged = reader.diff_summary(repo, DiffSpec(left=head, cached=True))
    assert [(item.path, item.insertions) for item in staged.files] == [("a.txt", 1)]
    worktree = reader.diff_summary(repo, DiffSpec(left=None, untracked=True))
    assert {item.path: item.insertions for item in worktree.files} == {"a.txt": 1, "b.txt": 1}
    hunks = reader.diff_file(repo, DiffSpec(left=None, untracked=True), "b.txt", 10)
    assert hunks.hunks[0].lines[0].text == "new"


def test_diff_handles_renames_binary_and_root_commit(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    root = commit(repo, "a.txt", "".join(f"{n}\n" for n in range(30)), "one")
    (repo / "bin.dat").write_bytes(b"\0\1\2")
    _ = git(repo, "mv", "a.txt", "z z.txt")
    _ = git(repo, "add", "bin.dat")
    _ = git(repo, "commit", "-qm", "move")
    second = git(repo, "rev-parse", "HEAD")
    reader = GitHistory()
    files = {item.path: item for item in reader.diff_summary(repo, DiffSpec(left=root, right=second)).files}
    assert files["z z.txt"].change == "renamed"
    assert files["z z.txt"].old_path == "a.txt"
    assert files["bin.dat"].change == "binary"
    empty = reader.empty_tree(repo)
    assert reader.parent_of(repo, root) is None
    first = reader.diff_summary(repo, DiffSpec(left=empty, right=root))
    assert first.files[0].insertions == 30


def test_a_failed_read_says_so(tmp_path: Path) -> None:
    with pytest.raises(GitCommandError):
        _ = GitHistory().refs(tmp_path)
    with pytest.raises(GitCommandError):
        _ = GitRunner(Path("/nonexistent/git")).read(tmp_path, "status")
    with pytest.raises(GitCommandError):
        _ = GitRunner().read(Path("relative"), "status")


def test_counts_add_up_the_lines_between_two_commits(tmp_path: Path) -> None:
    repo = new_repo(tmp_path / "repo")
    first = commit(repo, "lines.txt", "one\ntwo\n", "first")
    second = commit(repo, "lines.txt", "one\nchanged\nthree\n", "second")
    assert GitHistory(GitRunner()).counts(repo, first, second) == (2, 1)
