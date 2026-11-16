"""Scope checks treat git names literally and hooks cannot override a denied write."""

import asyncio
import subprocess
from datetime import UTC, datetime
lazy from collections.abc import Iterator
lazy from pathlib import Path

import msgspec
import pytest

from aconsole.core.application.ports.diff_reader import DiffReadError
from aconsole.core.application.services.approval_broker import ApprovalHold
from aconsole.core.bootstrap import Daemon
from aconsole.core.domain.approval_policy import ApprovalPolicyChoice, ApprovalPolicyMode, DangerousSubmode
from aconsole.core.domain.approvals import ApprovalBook
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.scope import check_scope
from aconsole.core.infrastructure.git.diff import GitDiff
from aconsole.core.infrastructure.http.approval import handle_permission
from aconsole.core.infrastructure.http.routes import IngestItem
from aconsole.core.infrastructure.mcp.tools import Claimed, NodeIn, PlanBoard, TabContext, _ToolError
from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from aconsole.core.infrastructure.persistence.models import NodeAttempt, Project
from aconsole.core.infrastructure.persistence.repositories.attempts import AttemptDatabase
from aconsole.core.infrastructure.persistence.repositories.plans import PlanDatabase
from aconsole.core.infrastructure.persistence.writer import Writer
from tests.gate_profiles import MICRO_PROFILE


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
            {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {}}
        ),
    )
    response = asyncio.run(handle_permission(item, hold, hold_seconds=1, worktree=str(tmp_path), scope_paths=("**",)))
    assert b"SCOPE_VIOLATION" in response.body


_AT = datetime(2026, 11, 15, tzinfo=UTC)
type ScopeBoard = tuple[PlanBoard, TabContext, str, Claimed, Path, Writer]


@pytest.fixture
def scope_board(tmp_path: Path) -> Iterator[ScopeBoard]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.test")
    (repo / "src").mkdir()
    (repo / "src/in.py").write_text("base\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    url = f"sqlite:///{tmp_path / 'audit.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    writer = Writer(engine)
    writer.start()
    writer.call(lambda s: s.add(Project(id="p", name="P", root_path=str(repo), languages=[], created_at=_AT)))
    board = PlanBoard(now=lambda: _AT)
    board.use_store(PlanDatabase(writer))
    history = AttemptDatabase(writer)
    board.use_attempts(history)
    ctx = TabContext(tab_id="t", project_id="p", worktree=repo, agent="claude")
    created = board.create(
        ctx,
        "Scope preview",
        "Check wiring",
        [
            NodeIn(key="D-0", title="Direction", objective="Scope", level=0, paths=["src/**", "tests/**"],
                   invariants="scope held", gate_profile=MICRO_PROFILE),
            NodeIn(key="C-1", title="Contract", objective="Change", parent="D-0", pipeline=True,
                   paths=["src/in.py"], acceptance=["scope held"], test_cmd="pytest"),
        ],
    )
    node = created.nodes[1].node_id
    board.freeze(created.plan_id, {}, graph_present=False)
    claim = board.claim(ctx, node)
    history.start(NodeAttempt(id="a", node_id=node, attempt_no=1, status="running", base_oid=base, started_at=_AT))
    try:
        yield board, ctx, node, claim, repo, writer
    finally:
        writer.stop()
        engine.dispose()


def test_verifying_uses_durable_attempt_base_and_persists_review(scope_board: ScopeBoard) -> None:
    board, ctx, node, claim, repo, writer = scope_board
    (repo / "tests").mkdir()
    (repo / "tests/out.py").write_text("outside\n")
    with pytest.raises(_ToolError, match="SCOPE_VIOLATION"):
        board.update(ctx, node, claim.fence, "verifying", None, "checked")
    view = board.views()[0]
    card = next(n for n in view.nodes if str(n.id) == node)
    assert board._plans[str(view.id)].dag.get("C-1").review_required
    assert card.status.value == "in_progress"
    restored = PlanBoard()
    restored.use_store(PlanDatabase(writer))
    assert restored._plans[str(view.id)].dag.get("C-1").review_required


def test_authorized_scope_still_requires_verification_and_wrong_project_has_no_scope(scope_board: ScopeBoard) -> None:
    board, ctx, node, claim, repo, _writer = scope_board
    (repo / "src/in.py").write_text("permitted\n")
    assert board.scope_for_tab("t", "p") == ("src/in.py",)
    assert board.scope_for_tab("t", "other") is None
    board.assert_scope(ctx, node)
    with pytest.raises(_ToolError, match="GATE_RECEIPT_REQUIRED"):
        board.update(ctx, node, claim.fence, "verifying", None, "checked")
    assert next(n for n in board.views()[0].nodes if str(n.id) == node).status.value == "in_progress"


def test_daemon_uses_active_node_scope_for_hook(scope_board: ScopeBoard, tmp_path: Path) -> None:
    board, _ctx, _node, _claim, repo, _writer = scope_board
    daemon = Daemon(tmp_path / "core.sock", database=tmp_path / "unused.db")
    daemon._plans = board
    daemon._permission_worktrees["t"] = str(repo)
    daemon._permission_projects["t"] = "p"
    item = IngestItem(
        kind="claude.hook",
        tab_id="t",
        body=msgspec.json.encode(
            {
                "session_id": "s",
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": str(repo / "outside.py"), "content": "denied"},
            }
        ),
    )
    response = asyncio.run(daemon._permission(item))
    assert b"SCOPE_VIOLATION" in response.body


def test_stale_fence_does_not_mutate_review(scope_board: ScopeBoard) -> None:
    board, ctx, node, claim, repo, _writer = scope_board
    (repo / "tests").mkdir()
    (repo / "tests/out.py").write_text("outside\n")
    with pytest.raises(_ToolError, match="LEASE_STALE_FENCE"):
        board.update(ctx, node, claim.fence - 1, "verifying", None, "checked")
    assert not next(iter(board._plans.values())).dag.get("C-1").review_required
