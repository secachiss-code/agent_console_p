"""Round 6 K23: a failed create is rolled back, setup runs only when approved, and the ini backup keeps the original."""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path

import pytest

from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.containers.distrobox_ini import DistroboxIni
from aconsole.core.infrastructure.git.worktrees import WorktreeError, WorktreeRequest, Worktrees

_GIT = Path("/usr/bin/git")
_EMAIL = "aconsole@localhost"
_NAME = "aconsole"
_CONFIRM_INVALID = 3007
_CHILD_TIMEOUT_S = 2.0
_WAIT_S = 1.0
_POLL_S = 0.02
_STATE_FIELD = 2
_ZOMBIE = "Z"


def test_k23_failed_setup_rolls_back_the_tree_branch_and_record(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    approved = _approved_setup(repo, "#!/bin/sh\nexit 9\n")
    dest = tmp_path / "wt"
    with pytest.raises(WorktreeError, match="setup script failed"):
        _ = _worktrees().create(_request(repo, dest, "agent", "roll", setup_sha256=approved))
    _assert_rolled_back(repo, dest, "ac/agent-roll")


def test_k23_setup_timeout_rolls_back_the_tree_branch_and_record(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    approved = _approved_setup(repo, "#!/bin/sh\nsleep 5\n")
    dest = tmp_path / "wt"
    with pytest.raises(WorktreeError, match="timed out"):
        _ = _worktrees().create(
            _request(repo, dest, "agent", "late", setup_timeout_s=0.05, setup_sha256=approved),
        )
    _assert_rolled_back(repo, dest, "ac/agent-late")


def test_k23_os_error_after_worktree_add_rolls_back(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    # A tracked file where the tab lock folder belongs: writing the lock fails after `worktree add`.
    _ = (repo / ".aconsole").write_text("a file, not a folder\n", encoding="utf-8")
    _git(repo, "add", ".aconsole")
    _commit(repo, "aconsole file")
    dest = tmp_path / "wt"
    with pytest.raises(FileExistsError):
        _ = _worktrees().create(_request(repo, dest, "agent", "oserr"))
    _assert_rolled_back(repo, dest, "ac/agent-oserr")


def test_k23_setup_timeout_kills_the_background_children(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    pid_file = tmp_path / "child.pid"
    approved = _approved_setup(repo, f"#!/bin/sh\nsleep 30 &\necho $! > {pid_file}\nsleep 30\n")
    dest = tmp_path / "wt"
    with pytest.raises(WorktreeError, match="timed out"):
        _ = _worktrees().create(
            _request(repo, dest, "agent", "group", setup_timeout_s=_CHILD_TIMEOUT_S, setup_sha256=approved),
        )
    child = int(pid_file.read_text(encoding="utf-8"))
    assert _wait_gone(child), "a background child of the setup script survived the timeout"
    _assert_rolled_back(repo, dest, "ac/agent-group")


def test_k23_setup_runs_only_with_the_approved_sha(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init(repo)
    marker = tmp_path / "ran.marker"
    approved = _approved_setup(repo, f"#!/bin/sh\ntouch {marker}\n")
    # The script changes after the user approved it: the approved sha no longer matches.
    _ = _approved_setup(repo, f"#!/bin/sh\ntouch {marker}\n# edited after approval\n", message="edit")
    trees = _worktrees()
    edited = trees.create(_request(repo, tmp_path / "edited", "agent", "edited", setup_sha256=approved))
    assert edited.setup_skipped is True
    assert trees.locked(edited.dest)
    assert not marker.exists()
    unknown = trees.create(_request(repo, tmp_path / "unknown", "agent", "unknown", setup_sha256="0" * 64))
    assert unknown.setup_skipped is True
    assert not marker.exists()
    none = trees.create(_request(repo, tmp_path / "none", "agent", "none"))
    assert none.setup_skipped is True
    assert not marker.exists()
    current = _sha(repo / ".aconsole" / "setup.sh")
    ran = trees.create(_request(repo, tmp_path / "ran", "agent", "ran", setup_sha256=current))
    assert ran.setup_skipped is False
    assert marker.is_file()


def test_k23_backup_is_the_original_from_the_first_apply(tmp_path: Path) -> None:
    before = b"[agent-claude]\r\nimage = base\r\n"
    first = "[agent-claude]\r\nimage = first\r\n"
    second = "[agent-claude]\r\nimage = second\r\n"
    ini = tmp_path / "distrobox.ini"
    _ = ini.write_bytes(before)
    book = DistroboxIni(ini)
    _ = book.preview(first, confirm="one")
    backup = book.apply(first, "one")
    assert backup == tmp_path / "distrobox.ini.bak"
    assert backup.read_bytes() == before
    assert ini.read_bytes() == first.encode("utf-8")
    _ = book.preview(second, confirm="two")
    _ = book.apply(second, "two")
    assert backup.read_bytes() == before
    assert ini.read_bytes() == second.encode("utf-8")


def test_k23_crlf_bytes_are_kept_exactly(tmp_path: Path) -> None:
    before = b"[agent-claude]\r\nimage = a\r\n\r\n[agent-codex]\r\nimage = b\r\n"
    proposed = "[agent-claude]\r\nimage = c\r\n\r\n[agent-codex]\r\nimage = b\r\n"
    ini = tmp_path / "distrobox.ini"
    _ = ini.write_bytes(before)
    book = DistroboxIni(ini)
    preview = book.preview(proposed, confirm="crlf")
    assert "+image = c" in preview.diff
    backup = book.apply(proposed, "crlf")
    assert backup.read_bytes() == before
    written = ini.read_bytes()
    assert written == proposed.encode("utf-8")
    assert written.count(b"\n") == written.count(b"\r\n")


def test_k23_apply_after_the_file_changed_is_refused_and_forgets_the_token(tmp_path: Path) -> None:
    before = b"[agent-claude]\r\nimage = a\r\n"
    proposed = "[agent-claude]\r\nimage = b\r\n"
    edited = b"[agent-claude]\r\nimage = someone else\r\n"
    ini = tmp_path / "distrobox.ini"
    _ = ini.write_bytes(before)
    book = DistroboxIni(ini)
    _ = book.preview(proposed, confirm="shown")
    _ = ini.write_bytes(edited)
    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID") as refused:
        _ = book.apply(proposed, "shown")
    assert refused.value.code == _CONFIRM_INVALID
    assert ini.read_bytes() == edited
    assert not (tmp_path / "distrobox.ini.bak").exists()
    _ = ini.write_bytes(before)
    with pytest.raises(DomainError, match="CONFIRM_TOKEN_INVALID"):
        _ = book.apply(proposed, "shown")
    assert ini.read_bytes() == before
    assert not (tmp_path / "distrobox.ini.bak").exists()


def _assert_rolled_back(repo: Path, dest: Path, branch: str) -> None:
    resolved = dest.resolve()
    assert not dest.exists()
    assert not _branch_listed(repo, branch)
    assert str(resolved) not in _git_text(repo, "worktree", "list", "--porcelain")
    folder = repo / ".git" / "aconsole-worktrees"
    if folder.is_dir():
        assert all(str(resolved) not in record.read_text(encoding="utf-8") for record in folder.iterdir())


def _wait_gone(pid: int) -> bool:
    deadline = time.monotonic() + _WAIT_S
    while True:
        if _dead(pid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_S)


def _dead(pid: int) -> bool:
    """Gone means `os.kill` finds no process; a zombie that nobody reaped has already exited."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
    except FileNotFoundError:
        return True
    return fields[_STATE_FIELD] == _ZOMBIE


def _worktrees() -> Worktrees:
    return Worktrees(_GIT)


def _request(
    repo: Path,
    dest: Path,
    slug: str,
    tab_id: str,
    *,
    setup_timeout_s: float = 30.0,
    setup_sha256: str | None = None,
) -> WorktreeRequest:
    return WorktreeRequest(
        repo=repo,
        dest=dest,
        slug=slug,
        tab_id=tab_id,
        setup_timeout_s=setup_timeout_s,
        setup_sha256=setup_sha256,
    )


def _approved_setup(repo: Path, body: str, *, message: str = "setup") -> str:
    """Commit `.aconsole/setup.sh` with `body` and return the sha256 of those bytes."""
    setup = repo / ".aconsole" / "setup.sh"
    setup.parent.mkdir(exist_ok=True)
    _ = setup.write_text(body, encoding="utf-8")
    _git(repo, "add", ".aconsole/setup.sh")
    _commit(repo, message)
    return _sha(setup)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _init(repo: Path) -> None:
    repo.mkdir()
    _git(repo.parent, "init", "-b", "main", str(repo))
    _ = (repo / "README").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "README")
    _commit(repo, "init")


def _commit(repo: Path, message: str) -> None:
    _git(repo, "-c", f"user.email={_EMAIL}", "-c", f"user.name={_NAME}", "commit", "-m", message)


def _branch_listed(repo: Path, branch: str) -> bool:
    return branch in _git_text(repo, "branch", "--list", branch).split()


def _git(repo: Path, *args: str) -> None:
    _ = _git_text(repo, *args)


def _git_text(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        [str(_GIT), "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        message = completed.stderr
        raise AssertionError(message)
    return completed.stdout
