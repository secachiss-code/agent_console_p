"""Diff review: rename, binary, a truncated large diff, and a driver that must not run."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from aconsole.contracts.events import InjectToTerminal
from aconsole.core.application.ports.diff_reader import DiffReadError
from aconsole.core.application.ports.events import EventSink
from aconsole.core.application.services.diff import DiffService
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.tab_status import TabStatus
from aconsole.core.infrastructure.git.diff import GitDiff

_GIT = "/usr/bin/git"
_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "diff"
_MARKER = "ACONSOLE_DIFF_DRIVER_RAN"
_QUEUED = "Комментарий поставлен в очередь вкладки"
_BIG = 20
_CAP = 4
_BETA = ((6, 10),)
_GAMMA = ((6, 11),)


def test_diff_review_rename_binary_truncation_and_driver(tmp_path: Path) -> None:
    repo, marker = _repo(tmp_path)
    os.environ[_MARKER] = str(marker)
    try:
        service = _service(repo)
        summary = service.summary("tab-1", None)
        empty_base = service.summary("tab-1", "")
        words = service.file("tab-1", "keep.txt", None)
        large = service.file("tab-1", "big.txt", None, max_lines=_CAP)
        binary = service.file("tab-1", "pic.bin", None)
    finally:
        os.environ.pop(_MARKER, None)
    assert not marker.exists()
    by_path = {item.path: item for item in summary.files}
    assert summary.base == "HEAD"
    assert summary.head == "WORKTREE"
    assert empty_base.base == "HEAD"
    assert by_path["new.txt"].change == "renamed"
    assert by_path["new.txt"].old_path == "old.txt"
    assert by_path["pic.bin"].change == "binary"
    assert by_path["pic.bin"].insertions == 0
    assert by_path["pic.bin"].deletions == 0
    assert by_path["keep.txt"].change == "modified"
    assert by_path["fresh.txt"].change == "added"
    assert by_path["gone.txt"].change == "deleted"
    assert ".gitattributes" not in by_path
    deleted = [line for hunk in words.hunks for line in hunk.lines if line.kind == "del"]
    added = [line for hunk in words.hunks for line in hunk.lines if line.kind == "add"]
    assert deleted[0].text == "alpha beta"
    assert deleted[0].word_spans == _BETA
    assert added[0].text == "alpha gamma"
    assert added[0].word_spans == _GAMMA
    assert words.hunks[0].id.startswith("@@")
    assert words.truncated is False
    shown = [line for hunk in large.hunks for line in hunk.lines if line.kind != "meta"]
    assert large.truncated is True
    assert shown
    assert len(shown) <= _CAP
    assert binary.hunks == ()
    assert binary.truncated is False


def test_diff_review_send_comment_queues_until_idle(tmp_path: Path) -> None:
    events = EventSink()
    service = DiffService(GitDiff(), events)
    service.bind("tab-1", tmp_path, TabStatus.WORKING)
    queued = service.send_comment("tab-1", "keep.txt", "@@ -1 +1 @@", "rename it")
    assert queued.ok is True
    assert queued.toast == _QUEUED
    assert events.events == []
    service.set_status("tab-1", TabStatus.IDLE)
    direct = service.send_comment("tab-1", "keep.txt", "@@ -1 +1 @@", "now")
    assert direct.toast is None
    assert len(events.events) == 2
    first = events.events[0]
    assert isinstance(first, InjectToTerminal)
    assert first.submit is True
    assert first.text == "Комментарий к `keep.txt` @@ -1 +1 @@: rename it\n"
    with pytest.raises(DomainError) as missing:
        service.summary("missing", None)
    assert missing.value.name == "TAB_NOT_FOUND"
    with pytest.raises(ValueError, match="max_lines"):
        service.file("tab-1", "keep.txt", None, max_lines=-1)


def test_diff_review_rejects_an_oversized_diff(tmp_path: Path) -> None:
    repo, marker = _repo(tmp_path)
    os.environ[_MARKER] = str(marker)
    try:
        service = DiffService(GitDiff(max_bytes=8), EventSink())
        service.bind("tab-1", repo, TabStatus.IDLE)
        with pytest.raises(DomainError) as caught:
            service.summary("tab-1", None)
    finally:
        os.environ.pop(_MARKER, None)
    assert not marker.exists()
    assert caught.value.name == "DIFF_TOO_LARGE"
    assert int(caught.value.extra[1]) > 8


def test_diff_review_rejects_a_relative_repo() -> None:
    with pytest.raises(DiffReadError):
        GitDiff().summary(Path("repo"), None)
    with pytest.raises(DiffReadError):
        GitDiff(Path("git"))
    with pytest.raises(DiffReadError):
        GitDiff(timeout_s=0)


def _service(repo: Path) -> DiffService:
    service = DiffService(GitDiff(), EventSink())
    service.bind("tab-1", repo, TabStatus.IDLE)
    return service


def _repo(tmp: Path) -> tuple[Path, Path]:
    repo = tmp / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "diff@example.com")
    _git(repo, "config", "user.name", "diff")
    (repo / "keep.txt").write_text("alpha beta\n")
    (repo / "old.txt").write_text("same\n")
    (repo / "gone.txt").write_text("gone\n")
    (repo / "pic.bin").write_bytes(b"a\0b")
    (repo / "big.txt").write_text("".join(f"line {number}\n" for number in range(_BIG)))
    (repo / ".gitattributes").write_text((_FIXTURE / "attributes").read_text())
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    driver = tmp / "driver.sh"
    driver.write_text((_FIXTURE / "driver.sh").read_text())
    driver.chmod(0o755)
    _git(repo, "config", "diff.aconsoledriver.command", str(driver))
    _git(repo, "config", "diff.aconsoledriver.textconv", str(driver))
    (repo / "keep.txt").write_text("alpha gamma\n")
    (repo / "gone.txt").unlink()
    (repo / "fresh.txt").write_text("new\n")
    (repo / "pic.bin").write_bytes(b"a\0c")
    (repo / "big.txt").write_text("".join(f"line {number}!\n" for number in range(_BIG)))
    _git(repo, "mv", "old.txt", "new.txt")
    _git(repo, "add", "-A")
    return repo, repo / "driver-ran"


def _git(repo: Path, *args: str) -> None:
    subprocess.run((_GIT, "-C", str(repo), *args), check=True, capture_output=True)
