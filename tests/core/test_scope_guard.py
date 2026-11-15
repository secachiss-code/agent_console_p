"""Scope checks treat git names literally and hooks cannot override a denied write."""

import asyncio
import subprocess
from datetime import UTC, datetime
lazy from pathlib import Path

import msgspec
import pytest

from aconsole.core.application.ports.diff_reader import DiffReadError
from aconsole.core.application.services.approval_broker import ApprovalHold
from aconsole.core.domain.approval_policy import ApprovalPolicyChoice, ApprovalPolicyMode, DangerousSubmode
from aconsole.core.domain.approvals import ApprovalBook
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.scope import check_scope
from aconsole.core.infrastructure.git.diff import GitDiff
from aconsole.core.infrastructure.http.approval import handle_permission
from aconsole.core.infrastructure.http.routes import IngestItem


@pytest.mark.parametrize(
    ("pattern", "path", "allowed"),
    [
        ("src/**", "src/a.py", True),
        ("src/**", "src/pkg/a.py", True),
        ("src/**/*.py", "src/a.py", True),
        ("src/*.py", "src/pkg/a.py", False),
        ("src/a.py", "src/a.py.bak", False),
        ("src/a.py", "src/a.py", True),
        ("src/[ab].py", "src/a.py", True),
        ("src/*", "src/[x].py", True),
        ("src/**", "src/../outside.py", False),
        ("**", "/outside.py", False),
        ("**", "src/./a.py", False),
        ("**", "src//a.py", False),
        ("**", "src\\a.py", False),
        ("**", "src/invalid\x00.py", False),
    ],
)
def test_scope_path_language(pattern: str, path: str, *, allowed: bool) -> None:
    result = check_scope((pattern,), (path,))
    assert (not result.violations) == allowed


def test_explicit_empty_scope_grants_no_writes_and_test_violation_requires_review() -> None:
    result = check_scope((), ("src/a.py", "tests/test_a.py"))
    assert result.review_required
    assert result.violations == ("src/a.py", "tests/test_a.py")
    with pytest.raises(DomainError, match="SCOPE_VIOLATION") as caught:
        result.require_allowed()
    assert caught.value.extra == result.violations
    assert not check_scope(("tests/**",), ("tests/test_a.py",)).review_required


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(("git", "-C", str(repo), *args), check=True, capture_output=True, text=True).stdout.strip()


def test_git_scope_includes_committed_staged_unstaged_untracked_and_both_rename_sides(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "scope@example.test")
    _git(tmp_path, "config", "user.name", "Scope test")
    for name in ("old.py", "staged.py", "working.py", "committed.py", "deleted.py"):
        (tmp_path / name).write_text("original\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "committed.py").write_text("committed\n")
    _git(tmp_path, "commit", "-qam", "attempt edit")
    _git(tmp_path, "mv", "old.py", "new.py")
    (tmp_path / "staged.py").write_text("staged\n")
    _git(tmp_path, "add", "staged.py")
    (tmp_path / "working.py").write_text("working\n")
    (tmp_path / "deleted.py").unlink()
    (tmp_path / "test with\nnewline.py").write_text("fresh\n")
    assert set(GitDiff().changed_paths(tmp_path, base)) == {
        "old.py",
        "new.py",
        "staged.py",
        "working.py",
        "committed.py",
        "deleted.py",
        "test with\nnewline.py",
    }


@pytest.mark.parametrize("base", ["HEAD", "--output=/outside", "abc"])
def test_diff_scope_rejects_non_oid_base(tmp_path: Path, base: str) -> None:
    with pytest.raises(DiffReadError, match="full object id"):
        GitDiff().changed_paths(tmp_path, base)


@pytest.mark.parametrize("kind", ["claude.hook", "codex.hook"])
@pytest.mark.parametrize("inline", [True, False])
@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_prewrite_deny_precedes_allow_policy_and_native_approval(
    tmp_path: Path,
    kind: str,
    tool: str,
    *,
    inline: bool,
) -> None:
    events: list[object] = []
    hold = ApprovalHold(
        ApprovalBook(), publish=events.append, notify=events.append, audit=events.append, now=lambda: datetime.now(UTC)
    )
    item = IngestItem(
        kind=kind,
        tab_id="tab",
        body=msgspec.json.encode(
            {
                "session_id": "s",
                "hook_event_name": "PreToolUse",
                "tool_name": tool,
                "tool_input": {
                    "file_path": str(tmp_path / "outside.py"),
                    "old_string": "old",
                    "new_string": "new",
                    "content": "new",
                    "edits": [],
                },
            }
        ),
    )
    response = asyncio.run(
        handle_permission(
            item,
            hold,
            hold_seconds=1,
            worktree=str(tmp_path),
            scope_paths=("src/**",),
            inline=inline,
            choice=ApprovalPolicyChoice(mode=ApprovalPolicyMode.ALLOW, dangerous=DangerousSubmode.ALLOW),
        )
    )
    assert b'"permissionDecision":"deny"' in response.body
    assert b"SCOPE_VIOLATION" in response.body
    assert not events


def test_prewrite_resolves_symlinks_before_scope_decision(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src/linked.py").symlink_to(tmp_path / "outside.py")
    hold = ApprovalHold(
        ApprovalBook(),
        publish=lambda _: None,
        notify=lambda _: None,
        audit=lambda _: None,
        now=lambda: datetime.now(UTC),
    )
    item = IngestItem(
        kind="claude.hook",
        tab_id="tab",
        body=msgspec.json.encode(
            {
                "session_id": "s",
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "cwd": str(repo),
                "tool_input": {"file_path": "src/linked.py", "content": "new"},
            }
        ),
    )
    response = asyncio.run(handle_permission(item, hold, hold_seconds=1, worktree=str(repo), scope_paths=("src/**",)))
    assert b"SCOPE_VIOLATION" in response.body


def test_unreadable_write_path_is_denied_even_with_wildcard_scope(tmp_path: Path) -> None:
    hold = ApprovalHold(ApprovalBook(), publish=lambda _: None, notify=lambda _: None,
        audit=lambda _: None, now=lambda: datetime.now(UTC))
    item = IngestItem(kind="claude.hook", tab_id="tab", body=msgspec.json.encode({
        "session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {}}))
    response = asyncio.run(handle_permission(item, hold, hold_seconds=1, worktree=str(tmp_path), scope_paths=("**",)))
    assert b"SCOPE_VIOLATION" in response.body
