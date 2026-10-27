"""Round 6 regression tests, written test-first. Each test pins one confirmed defect (docs/plan/ROUND6.md).

D1: glob overlap with wildcards on both sides. D2: a stale fence accepted after a lease expired.
D3: `git diff`, `git show`, `git log -p` in the prefix allowlist, and deny rules weaker than allow rules.
D4: disjoint user and app vault edits must both survive a merge.
"""

import fnmatch
import posixpath
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aconsole.core.domain.approvals import (
    ApprovalBook,
    ApprovalDecision,
    ApprovalPhase,
    DecisionBy,
    OpenRequest,
    PathScope,
    digest_input,
    prefix_matches,
)
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.plan import Move, NodeSpec, NodeStatus, Plan, Role, globs_overlap
from aconsole.core.domain.vault_sync import Note, Owners, merge

AT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
HOLD = timedelta(seconds=60)
LEASE_LAPSED = AT + timedelta(minutes=16)


def _node(key: str, paths: tuple[str, ...] = ()) -> NodeSpec:
    return NodeSpec(key=key, created_at=AT, paths=paths)


# D1: glob overlap


@pytest.mark.parametrize(
    ("left", "right", "overlaps"),
    [
        ("a*", "*b", True),
        ("src/*.py", "src/a*", True),
        ("*.py", "*.txt", False),
        ("src/**", "src/a.py", True),
        ("a?c", "abc", True),
        ("[ab]x", "bx", True),
    ],
)
def test_round6_glob_overlap_with_wildcards_on_both_sides(left: str, right: str, *, overlaps: bool) -> None:
    assert globs_overlap(left, right) is overlaps
    assert globs_overlap(right, left) is overlaps


_SEGMENT = st.text(alphabet="ab", min_size=1, max_size=3)


def _wild_segment(draw: st.DrawFn, segment: str) -> str:
    """Keep the segment, replace some characters by `?` or `*`, or replace the whole segment by `*`."""
    if draw(st.integers(min_value=0, max_value=3)) == 0:
        return "*"
    return "".join(draw(st.sampled_from((char, char, "?", "*"))) for char in segment)


@st.composite
def _path_and_two_patterns(draw: st.DrawFn) -> tuple[str, str, str]:
    """A concrete path, and two patterns that both match it (built by wildcarding the same segments)."""
    segments = draw(st.lists(_SEGMENT, min_size=1, max_size=3))
    left = "/".join(_wild_segment(draw, segment) for segment in segments)
    right = "/".join(_wild_segment(draw, segment) for segment in segments)
    return "/".join(segments), left, right


def _matches(pattern: str, path: str) -> bool:
    """The module's matching rule: one fnmatch per `/` segment, with the same segment count."""
    pattern_parts = pattern.split("/")
    path_parts = path.split("/")
    return len(pattern_parts) == len(path_parts) and all(
        fnmatch.fnmatchcase(part, pat) for part, pat in zip(path_parts, pattern_parts, strict=True)
    )


@given(_path_and_two_patterns())
@settings(max_examples=200)
def test_round6_overlap_property_when_one_path_matches_both(case: tuple[str, str, str]) -> None:
    path, left, right = case
    assert _matches(left, path)
    assert _matches(right, path)
    assert globs_overlap(left, right) is True
    assert globs_overlap(right, left) is True


@pytest.mark.parametrize(("left", "right"), [("a*", "*b"), ("src/*.py", "src/a*")])
def test_round6_claim_refuses_wildcard_paths_that_share_a_file(left: str, right: str) -> None:
    plan = Plan()
    _ = plan.add(_node("N-1", (left,)))
    _ = plan.add(_node("N-2", (right,)))
    _ = plan.claim("N-1", tab_id="tab-a", at=AT)
    with pytest.raises(DomainError, match="PATHS_OVERLAP"):
        _ = plan.claim("N-2", tab_id="tab-b", at=AT + timedelta(seconds=1))


# D2: stale fence after the lease has expired


def test_round6_stale_fence_rejected_after_the_lease_lapses_by_time() -> None:
    plan = Plan()
    _ = plan.add(_node("N-1"))
    claimed = plan.claim("N-1", tab_id="tab", at=AT)
    assert claimed.fence == 1
    with pytest.raises(DomainError, match="LEASE_STALE_FENCE"):
        _ = plan.transition(
            Move(key="N-1", to=NodeStatus.BLOCKED, role=Role.AGENT, at=LEASE_LAPSED, fence=claimed.fence)
        )
    assert plan.get("N-1").status is NodeStatus.PENDING


def test_round6_stale_fence_rejected_after_explicit_expiry() -> None:
    plan = Plan()
    _ = plan.add(_node("N-1"))
    claimed = plan.claim("N-1", tab_id="tab", at=AT)
    assert plan.expire_leases(LEASE_LAPSED) == ("N-1",)
    with pytest.raises(DomainError, match="LEASE_STALE_FENCE"):
        _ = plan.transition(
            Move(
                key="N-1",
                to=NodeStatus.BLOCKED,
                role=Role.AGENT,
                at=LEASE_LAPSED + timedelta(minutes=1),
                fence=claimed.fence,
            )
        )
    assert plan.get("N-1").status is NodeStatus.PENDING


@pytest.mark.parametrize("lapse", ["time", "explicit"])
def test_round6_fence_less_agent_move_after_a_lapse_follows_the_rules_alone(lapse: str) -> None:
    plan = Plan()
    _ = plan.add(_node("N-1"))
    _ = plan.claim("N-1", tab_id="tab", at=AT)
    if lapse == "explicit":
        assert plan.expire_leases(LEASE_LAPSED) == ("N-1",)
    blocked = plan.transition(
        Move(key="N-1", to=NodeStatus.BLOCKED, role=Role.AGENT, at=LEASE_LAPSED + timedelta(minutes=1), fence=None)
    )
    assert blocked.status is NodeStatus.BLOCKED


def test_round6_current_fence_inside_the_lease_is_accepted() -> None:
    plan = Plan()
    _ = plan.add(_node("N-1"))
    claimed = plan.claim("N-1", tab_id="tab", at=AT)
    released = plan.transition(
        Move(key="N-1", to=NodeStatus.PENDING, role=Role.AGENT, at=AT + timedelta(seconds=1), fence=claimed.fence)
    )
    assert released.status is NodeStatus.PENDING


def test_round6_never_claimed_node_needs_no_fence() -> None:
    plan = Plan()
    _ = plan.add(_node("N-1"))
    blocked = plan.transition(Move(key="N-1", to=NodeStatus.BLOCKED, role=Role.AGENT, at=AT))
    assert blocked.status is NodeStatus.BLOCKED


# D3: prefix allowlist for git diff, git show, git log -p

_SCOPE = PathScope(cwd="/wt", worktree="/wt", resolver=None)


class _Resolver:
    """Stands in for the disk: a relative word is a file under `cwd`, with no links."""

    def resolve(self, path: str, cwd: str) -> str:
        return posixpath.normpath(posixpath.join(cwd, path))


_SHOW_SCOPE = PathScope(cwd="/wt", worktree="/wt", resolver=_Resolver())


@pytest.mark.parametrize(
    "command",
    [
        "git diff --stat",
        "git diff --cached --name-only",
        "git diff --no-textconv --stat",
        "git diff --no-ext-diff --stat",
    ],
)
def test_round6_git_diff_prefix_needs_both_textconv_flags(command: str) -> None:
    assert prefix_matches(command, "git diff", scope=_SCOPE) is False


@pytest.mark.parametrize(
    "command",
    ["git diff --no-ext-diff --no-textconv --stat", "git diff --no-textconv --no-ext-diff --cached"],
)
def test_round6_git_diff_prefix_allows_both_textconv_flags(command: str) -> None:
    assert prefix_matches(command, "git diff", scope=_SCOPE) is True


@pytest.mark.parametrize("command", ["git show HEAD", "git show HEAD --stat"])
def test_round6_git_show_prefix_needs_both_textconv_flags(command: str) -> None:
    assert prefix_matches(command, "git show", scope=_SHOW_SCOPE) is False


def test_round6_git_show_prefix_allows_both_textconv_flags() -> None:
    assert prefix_matches("git show HEAD --no-ext-diff --no-textconv", "git show", scope=_SHOW_SCOPE) is True


@pytest.mark.parametrize("command", ["git log -p", "git log --oneline -p", "git log -p --stat"])
def test_round6_git_log_patch_prefix_needs_both_textconv_flags(command: str) -> None:
    assert prefix_matches(command, "git log", scope=_SCOPE) is False


def test_round6_git_log_patch_prefix_allows_both_textconv_flags() -> None:
    assert prefix_matches("git log -p --no-ext-diff --no-textconv", "git log", scope=_SCOPE) is True


def test_round6_git_log_without_a_patch_stays_covered_by_its_prefix() -> None:
    assert prefix_matches("git log --oneline", "git log", scope=_SCOPE) is True


# D3: a remembered deny is stronger than a broader allow


def _open_request(approval_id: str, command: str, at: datetime) -> OpenRequest:
    return OpenRequest(
        approval_id=approval_id,
        nonce=f"nonce-{approval_id}",
        tab_id="tab",
        session_id="sess",
        tool="Bash",
        command=command,
        input_sha256=digest_input(f"{approval_id}:{command}".encode()),
        risk=frozenset(),
        at=at,
        expires_at=at + HOLD,
        allow_tab_session=True,
    )


def test_round6_remembered_deny_beats_a_later_broader_allow_rule() -> None:
    book = ApprovalBook()
    _ = book.open(_open_request("deny-1", "git status --short", AT))
    denied = book.decide("deny-1", nonce="nonce-deny-1", decision=ApprovalDecision.DENY, remember_prefix=True, at=AT)
    assert denied.phase is ApprovalPhase.DENIED
    later = AT + timedelta(seconds=1)
    _ = book.open(_open_request("allow-1", "git status", later))
    _ = book.decide("allow-1", nonce="nonce-allow-1", decision=ApprovalDecision.ALLOW, remember_prefix=True, at=later)
    again = book.open(_open_request("deny-2", "git status --short", AT + timedelta(seconds=2)))
    assert again.phase is ApprovalPhase.DENIED
    assert again.by is DecisionBy.RULE


# D4: disjoint user and app edits of one vault note

_USER_SIDE = ("title", "objective", "acceptance", "review_required", "paths")
_APP_SIDE = ("tags", "lease", "updated", "owner_agent")


def _paint(note: Note, field: str, marker: str) -> Note:
    painted: dict[str, Note] = {
        "title": replace(note, title=marker),
        "objective": replace(note, objective=marker),
        "acceptance": replace(note, acceptance=(marker,)),
        "review_required": replace(note, review_required=not note.review_required),
        "paths": replace(note, paths=(marker,)),
        "tags": replace(note, tags=(marker,)),
        "lease": replace(note, lease=marker),
        "updated": replace(note, updated=marker),
        "owner_agent": replace(note, owner_agent=marker),
    }
    found = painted.get(field)
    if found is None:
        message = f"no painter for {field}"
        raise ValueError(message)
    return found


def _edit(note: Note, fields: tuple[str, ...], marker: str) -> Note:
    current = note
    for field in fields:
        current = _paint(current, field, marker)
    return current


@st.composite
def _disjoint_edits(draw: st.DrawFn) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The user edits user-owned fields and `paths`; the app edits app-owned fields and `owner_agent`."""
    user = draw(st.lists(st.sampled_from(_USER_SIDE), unique=True))
    app = draw(st.lists(st.sampled_from(_APP_SIDE), unique=True))
    return tuple(user), tuple(app)


_BASE = Note(title="Task", objective="Ship it", status="pending")


@given(_disjoint_edits())
@settings(max_examples=100)
def test_round6_disjoint_user_and_app_edits_both_survive(edits: tuple[tuple[str, ...], tuple[str, ...]]) -> None:
    user_fields, app_fields = edits
    owners = Owners()
    user_note = _edit(_BASE, user_fields, "user")
    app_note = _edit(_BASE, app_fields, "app")
    expected = _edit(user_note, app_fields, "app")
    decision = merge(_BASE, user_note, app_note, owners)
    assert decision.apply == expected
    assert decision.conflicts == ()
    assert decision.restore_fields == ()
    # Applying the two sides one after the other reaches the same note, whichever side goes first.
    after_user = merge(_BASE, user_note, _BASE, owners).apply
    user_then_app = merge(after_user, after_user, _edit(after_user, app_fields, "app"), owners).apply
    after_app = merge(_BASE, _BASE, app_note, owners).apply
    app_then_user = merge(after_app, _edit(after_app, user_fields, "user"), after_app, owners).apply
    assert user_then_app == expected
    assert app_then_user == expected
