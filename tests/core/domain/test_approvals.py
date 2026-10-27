"""Approval lifecycle, dedup, nonce, and exact command-prefix rules."""

import posixpath
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from aconsole.core.domain.approvals import (
    ApprovalBook,
    ApprovalDecision,
    ApprovalPhase,
    DecisionBy,
    OpenRequest,
    PathScope,
    _paths_inside,  # pyright: ignore[reportPrivateUsage] -- the resolver check is tested directly
    deny_matches,
    digest_input,
    prefix_matches,
)
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.risk import RiskTag

AT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
HOLD = timedelta(seconds=60)


def _request() -> OpenRequest:
    return OpenRequest(
        approval_id="a1",
        nonce="n1",
        tab_id="tab",
        session_id="sess",
        tool="Bash",
        command="rm -rf build",
        input_sha256="abc",
        risk=frozenset({RiskTag.DESTRUCTIVE}),
        at=AT,
        expires_at=AT + HOLD,
        allow_tab_session=True,
    )


def _open(book: ApprovalBook, request: OpenRequest | None = None) -> None:
    _ = book.open(_request() if request is None else request)


class _FakeResolver:
    """Stands in for the disk. A word in `real` resolves to its mapped path. Any other word is a file under `cwd`."""

    def __init__(self, real: dict[str, str] | None = None) -> None:
        self._real: dict[str, str] = {} if real is None else real

    def resolve(self, path: str, cwd: str) -> str:
        return self._real.get(path, posixpath.normpath(posixpath.join(cwd, path)))


def _covered(
    rule: str,
    command: str,
    *,
    cwd: str = "",
    worktree: str = "",
    resolver: _FakeResolver | None = None,
) -> bool:
    """True when a remembered `rule` allows `command` as a new approval with no caller-supplied risk."""
    book = ApprovalBook(cwd=cwd, worktree=worktree, resolver=resolver)
    _open(book, replace(_request(), command=rule, risk=frozenset()))
    _ = book.decide("a1", nonce="n1", decision=ApprovalDecision.ALLOW, remember_prefix=True, at=AT)
    later = AT + timedelta(seconds=1)
    opened = book.open(
        replace(
            _request(),
            approval_id="a2",
            nonce="n2",
            command=command,
            input_sha256="s2",
            risk=frozenset(),
            at=later,
            expires_at=later + HOLD,
        )
    )
    return opened.phase is ApprovalPhase.ALLOWED and opened.by is DecisionBy.RULE


def test_digest_and_prefix() -> None:
    assert digest_input(b"rm") == digest_input(b"rm")
    assert len(digest_input(b"rm")) == 64
    assert prefix_matches("rm -rf build/out", "rm -rf build") is False
    assert prefix_matches("rm -rf build2", "rm -rf build") is False
    assert prefix_matches("rmdir build", "rm") is False
    assert prefix_matches("rm -rf build", "") is False


def test_decide_remember_release_and_reject_bad_nonce() -> None:
    book = ApprovalBook()
    _open(book)
    with pytest.raises(DomainError, match="NONCE_MISMATCH"):
        _ = book.decide("a1", nonce="other", decision=ApprovalDecision.ALLOW, remember_prefix=False, at=AT)
    assert book.get("a1").phase is ApprovalPhase.PENDING
    allowed = book.decide("a1", nonce="n1", decision=ApprovalDecision.ALLOW, remember_prefix=True, at=AT)
    assert allowed.phase is ApprovalPhase.ALLOWED
    assert allowed.by is DecisionBy.USER
    with pytest.raises(DomainError, match="ALREADY_DECIDED"):
        _ = book.release("a1", nonce="n1", at=AT)


def test_remember_deny_auto_denies_the_same_command_from_a_new_request() -> None:
    book = ApprovalBook()
    _open(book)
    denied = book.decide("a1", nonce="n1", decision=ApprovalDecision.DENY, remember_prefix=True, at=AT)
    assert denied.phase is ApprovalPhase.DENIED
    assert denied.by is DecisionBy.USER
    later = AT + timedelta(seconds=1)
    again = book.open(
        replace(_request(), approval_id="a2", nonce="n2", input_sha256="s2", at=later, expires_at=later + HOLD)
    )
    assert again.phase is ApprovalPhase.DENIED
    assert again.by is DecisionBy.RULE


def test_deny_rule_covers_a_longer_command_only_after_a_blank() -> None:
    book = ApprovalBook()
    _open(book)
    _ = book.decide("a1", nonce="n1", decision=ApprovalDecision.DENY, remember_prefix=True, at=AT)
    later = AT + timedelta(seconds=1)
    spaced = book.open(
        replace(
            _request(),
            approval_id="a2",
            nonce="n2",
            command="rm -rf build now",
            input_sha256="s2",
            at=later,
            expires_at=later + HOLD,
        )
    )
    glued = book.open(
        replace(
            _request(),
            approval_id="a3",
            nonce="n3",
            command="rm -rf build/out",
            input_sha256="s3",
            at=later,
            expires_at=later + HOLD,
        )
    )
    assert spaced.phase is ApprovalPhase.DENIED
    assert spaced.by is DecisionBy.RULE
    assert glued.phase is ApprovalPhase.PENDING
    assert glued.by is None


def test_deny_rule_stays_in_its_own_tab_and_tool() -> None:
    book = ApprovalBook()
    _open(book)
    _ = book.decide("a1", nonce="n1", decision=ApprovalDecision.DENY, remember_prefix=True, at=AT)
    later = AT + timedelta(seconds=1)
    other_tab = book.open(
        replace(
            _request(),
            approval_id="a2",
            nonce="n2",
            tab_id="tab-2",
            session_id="sess-2",
            input_sha256="s2",
            at=later,
            expires_at=later + HOLD,
        )
    )
    other_tool = book.open(
        replace(
            _request(),
            approval_id="a3",
            nonce="n3",
            tool="Read",
            session_id="sess-3",
            input_sha256="s3",
            at=later,
            expires_at=later + HOLD,
        )
    )
    assert other_tab.phase is ApprovalPhase.PENDING
    assert other_tool.phase is ApprovalPhase.PENDING


def test_remember_deny_needs_the_tab_session_offer() -> None:
    book = ApprovalBook()
    _open(book, replace(_request(), allow_tab_session=False))
    with pytest.raises(ValueError, match="tab_session"):
        _ = book.decide("a1", nonce="n1", decision=ApprovalDecision.DENY, remember_prefix=True, at=AT)
    assert book.get("a1").phase is ApprovalPhase.PENDING


@pytest.mark.parametrize(
    ("command", "prefix", "covered"),
    [
        ("rm x", "rm x", True),
        ("rm x", "rm", True),
        ("rm x ls", "rm x", True),
        ("rm x\tls", "rm x", True),
        ("  rm x  ", "rm x", True),
        ("rm x", "  rm x\t", True),
        ("rm xy", "rm x", False),
        ("rm x;ls", "rm x", False),
        ("rm x&ls", "rm x", False),
        ("rm x\u00a0y", "rm x", False),
        ("rmx", "rm", False),
        ("rm", "rm x", False),
        ("ls x", "rm x", False),
        ("rm x", "", False),
        ("rm x", "   ", False),
    ],
)
def test_deny_matches_the_same_text_or_a_prefix_then_a_blank(command: str, prefix: str, *, covered: bool) -> None:
    assert deny_matches(command, prefix) is covered


def test_rule_allows_only_the_same_prefix() -> None:
    book = ApprovalBook()
    _open(book, replace(_request(), command="rm -rf build"))
    _ = book.decide("a1", nonce="n1", decision=ApprovalDecision.ALLOW, remember_prefix=True, at=AT)
    again = book.open(
        replace(
            _request(),
            approval_id="a2",
            nonce="n2",
            session_id="sess-2",
            command="rm -rf build/out",
            input_sha256="other",
            at=AT + timedelta(seconds=1),
            expires_at=AT + timedelta(seconds=1) + HOLD,
        )
    )
    unrelated = book.open(
        replace(
            _request(),
            approval_id="a3",
            nonce="n3",
            session_id="sess-3",
            command="rm -rf /",
            input_sha256="third",
            at=AT + timedelta(seconds=2),
            expires_at=AT + timedelta(seconds=2) + HOLD,
        )
    )
    # `build/out` is not a separate word, so the rule does not cover it.
    assert again.phase is ApprovalPhase.PENDING
    assert again.by is None
    assert unrelated.phase is ApprovalPhase.PENDING


def test_risky_command_matches_only_an_exact_rule() -> None:
    book = ApprovalBook(cwd="/wt", worktree="/wt", resolver=_FakeResolver())
    _open(book, replace(_request(), command="git status", risk=frozenset()))
    _ = book.decide("a1", nonce="n1", decision=ApprovalDecision.ALLOW, remember_prefix=True, at=AT)
    later = AT + timedelta(seconds=1)
    extra = book.open(
        replace(
            _request(),
            approval_id="a2",
            nonce="n2",
            command="git status short",
            input_sha256="s2",
            risk=frozenset(),
            at=later,
            expires_at=later + HOLD,
        )
    )
    tagged_extra = book.open(
        replace(
            _request(),
            approval_id="a3",
            nonce="n3",
            command="git status short",
            input_sha256="s3",
            risk=frozenset({RiskTag.NETWORK}),
            at=later,
            expires_at=later + HOLD,
        )
    )
    tagged_exact = book.open(
        replace(
            _request(),
            approval_id="a4",
            nonce="n4",
            command="git status",
            input_sha256="s4",
            risk=frozenset({RiskTag.NETWORK}),
            at=later,
            expires_at=later + HOLD,
        )
    )
    assert extra.phase is ApprovalPhase.ALLOWED
    assert extra.by is DecisionBy.RULE
    assert tagged_extra.phase is ApprovalPhase.PENDING
    assert tagged_exact.phase is ApprovalPhase.ALLOWED
    assert tagged_exact.by is DecisionBy.RULE


def test_prefix_extends_only_with_plain_words() -> None:
    assert prefix_matches("git status", "git status") is True
    # `short` is a path word, so without a resolver the prefix does not cover it.
    assert prefix_matches("git status short", "git status") is False
    assert prefix_matches("git statusx", "git status") is False
    # `build` is a path word, and without a resolver a path word is never covered by a prefix.
    assert prefix_matches("cat build out", "cat") is False
    # `cat build` is not an allowlist entry, so it is an exact-only rule.
    assert prefix_matches("cat build out", "cat build") is False
    assert prefix_matches("git status --short", "git status") is True
    assert prefix_matches("git status short", "git status", exact_only=True) is False
    assert prefix_matches("git status", "git status", exact_only=True) is True


def test_prefix_refuses_a_command_that_classify_tags() -> None:
    repo = PathScope(cwd="/repo", worktree="/repo")
    assert prefix_matches("rm build", "rm", scope=repo) is False
    assert prefix_matches("pip install requests", "pip", scope=repo) is False
    inside = PathScope(cwd="/repo", worktree="/repo", resolver=_FakeResolver({"build": "/repo/build"}))
    assert prefix_matches("cat build", "cat", scope=inside) is True


@pytest.mark.parametrize(
    "command",
    [
        "git status && rm -rf ~",
        "git status; rm -rf ~",
        "git status | sh",
        "git status || rm -rf ~",
        "git status & rm -rf ~",
        "git status > out.txt",
        "git status < in.txt",
        "git status $(rm -rf ~)",
        "git status `rm -rf ~`",
        "git status\nrm -rf ~",
        "git status ../../x",
        "git status/../../x",
        "git status 'unclosed",
    ],
)
def test_prefix_refuses_operators_parent_segments_and_bad_quotes(command: str) -> None:
    assert prefix_matches(command, "git status") is False


def test_prefix_refuses_a_path_extension_and_chained_commands() -> None:
    assert prefix_matches("rm -rf build/../../x", "rm -rf build") is False
    assert prefix_matches("rm -rf build && rm -rf ~", "rm -rf build") is False


@pytest.mark.parametrize(
    ("rule", "command"),
    [
        ("git", "git -c core.fsmonitor=evil status"),
        ("tar", "tar -C.. -xf a.tar"),
        ("cat", "cat /etc/shadow=x"),
        ("docker", "docker --mount type=bind,src=/,dst=/h alpine"),
        ("cat", "cat a=b"),
        ("cat", "cat a..b"),
        ("cat", "cat .hidden"),
        ("cat", "cat @x"),
        ("cat", "cat a#b"),
        ("rm", "rm build"),
        ("pip", "pip install requests"),
    ],
)
def test_prefix_rule_does_not_cover_option_path_assignment_or_tagged_words(rule: str, command: str) -> None:
    assert _covered(rule, command) is False


def test_bare_program_rule_is_exact_only() -> None:
    assert _covered("git", "git") is True
    assert _covered("git", "git status") is False


def test_rule_for_a_program_does_not_cover_its_network_or_config_write() -> None:
    assert _covered("npm", "npm i evil") is False
    assert _covered("git", "git config core.pager evil") is False
    assert _covered("git status", "git status") is True


@pytest.mark.parametrize(
    ("rule", "command", "expected"),
    [
        ("rm -rf ~", "rm -rf ~\xa0", False),
        ("git status", "git status\xa0", False),
        ("git status", "git status short\u2003x", False),
        ("git status", "git status\tshort", False),
        ("rm -rf ~\xa0", "rm -rf ~\xa0", True),
        ("git status", " git status ", True),
        ("git status", "git status   -s", True),
    ],
)
def test_blanks_are_ascii_space_and_tab_only(rule: str, command: str, *, expected: bool) -> None:
    assert prefix_matches(command, rule) is expected


@pytest.mark.parametrize(
    ("rule", "command"),
    [
        ("cat README.md", "cat README.md {x,/etc/shadow}"),
        ("cat README.md", "cat README.md {..,}/etc/shadow"),
        ("cat README.md", "cat README.md $HOME/.ssh/id_ed25519"),
        ("cat README.md", "cat README.md ${PWD%/*}/.ssh/id_ed25519"),
        ("cat README.md", "cat README.md /etc/shadow"),
        ("find . -name x", "find . -name x -delete"),
        ("find . -name x", "find . -name x -exec sh -c 'rm -rf ~' +"),
        ("bash", "bash -lc 'rm -rf ~'"),
        ("env", "env sh -c 'rm -rf ~'"),
        ("env", "env -S 'rm -rf ~'"),
        ("echo '$(uname)'", "echo $(uname)"),
        ("rm -rf '~'", "rm -rf ~"),
        ("rm -rf '*'", "rm -rf *"),
        ("sh", "sh -c 'rm -rf x"),
    ],
)
def test_remembered_rule_does_not_cover_bypass_commands(rule: str, command: str) -> None:
    assert _covered(rule, command) is False


def test_exact_rule_and_plain_prefix_cover_their_commands() -> None:
    assert _covered("git status", "git status") is True
    assert _covered("git status", "git status short", cwd="/wt", worktree="/wt", resolver=_FakeResolver()) is True
    assert _covered("rm -rf build", "rm -rf build") is True


def test_symlink_in_the_worktree_that_points_outside_is_refused() -> None:
    resolver = _FakeResolver({"link": "/etc/hostname"})
    assert _covered("cat", "cat link", cwd="/wt", worktree="/wt", resolver=resolver) is False


def test_slash_path_word_is_refused_by_the_allowlist_before_the_resolver() -> None:
    resolver = _FakeResolver({"sub/link": "/etc/hostname"})
    assert _covered("cat", "cat sub/link", cwd="/wt", worktree="/wt", resolver=resolver) is False


def test_subdirectory_symlink_is_refused_by_the_resolver_itself() -> None:
    scope = PathScope(cwd="/wt", worktree="/wt", resolver=_FakeResolver({"sub/link": "/etc/hostname"}))
    assert _paths_inside(("sub/link",), scope) is False


def test_subdirectory_file_that_resolves_inside_the_worktree_is_inside() -> None:
    scope = PathScope(cwd="/wt", worktree="/wt", resolver=_FakeResolver({"sub/file": "/wt/sub/file"}))
    assert _paths_inside(("sub/file",), scope) is True


def test_worktree_that_is_a_symlink_still_bounds_its_own_files() -> None:
    resolver = _FakeResolver({"/wt": "/real/wt", "ok.txt": "/real/wt/ok.txt"})
    assert _covered("cat", "cat ok.txt", cwd="/wt", worktree="/wt", resolver=resolver) is True


def test_symlinked_worktree_does_not_admit_a_file_outside_its_target() -> None:
    resolver = _FakeResolver({"/wt": "/real/wt", "other.txt": "/real/other/other.txt"})
    assert _covered("cat", "cat other.txt", cwd="/wt", worktree="/wt", resolver=resolver) is False


def test_identical_command_is_rechecked_when_a_link_now_points_outside() -> None:
    scope = PathScope(cwd="/wt", worktree="/wt", resolver=_FakeResolver({"rt": "/outside/secret.txt"}))
    assert prefix_matches("cat rt", "cat rt", scope=scope) is False


def test_identical_command_without_path_words_needs_no_resolver() -> None:
    assert prefix_matches("rm -rf build", "rm -rf build", scope=PathScope()) is True


def test_path_word_resolving_inside_the_worktree_is_covered() -> None:
    resolver = _FakeResolver({"README.md": "/wt/README.md"})
    assert _covered("cat", "cat README.md", cwd="/wt", worktree="/wt", resolver=resolver) is True


def test_path_word_that_climbs_out_is_refused_whatever_the_resolver_says() -> None:
    resolver = _FakeResolver({"sub/../../x": "/wt/x"})
    assert _covered("cat", "cat sub/../../x", cwd="/wt", worktree="/wt", resolver=resolver) is False


def test_sibling_directory_with_the_same_prefix_is_not_inside_the_worktree() -> None:
    resolver = _FakeResolver({"x": "/wt2/x"})
    assert _covered("cat", "cat x", cwd="/wt", worktree="/wt", resolver=resolver) is False


def test_path_word_after_double_dash_is_checked() -> None:
    resolver = _FakeResolver({"link": "/etc/hostname"})
    assert _covered("cat", "cat -- link", cwd="/wt", worktree="/wt", resolver=resolver) is False


def test_path_words_need_a_resolver_and_a_cwd() -> None:
    assert _covered("cat", "cat README.md", cwd="/wt", worktree="/wt") is False
    assert _covered("cat README.md", "cat README.md", cwd="/wt", worktree="/wt") is False
    assert _covered("cat", "cat README.md", cwd="", worktree="/wt", resolver=_FakeResolver()) is False
    assert _covered("cat", "cat README.md", cwd="/wt", worktree="", resolver=_FakeResolver()) is False


def test_commands_without_path_words_need_no_resolver() -> None:
    assert _covered("ls", "ls -l") is True
    assert _covered("git status", "git status --short") is True


@pytest.mark.parametrize(
    ("rule", "command", "expected"),
    [
        ("git status", "git status -s", True),
        ("git status", "git status --short", True),
        ("git status", "git status -- src/app.py", False),
        ("cat", "cat a/b", False),
        ("git", "git status", False),
        ("npm test", "npm test x", False),
        ("git branch", "git branch -D x", False),
        ("git diff", "git diff --output=x", False),
        ("git", "git -c core.pager=x status", False),
        ("git status", "git -c core.pager=x status", False),
        ("ls", "ls -l", True),
        ("ls", "ls -x", False),
    ],
)
def test_end_to_end_prefix_rules_through_the_book(rule: str, command: str, *, expected: bool) -> None:
    assert _covered(rule, command) is expected


def test_duplicate_input_during_hold_is_the_same_approval() -> None:
    book = ApprovalBook()
    _open(book)
    later = AT + timedelta(seconds=5)
    second = book.open(replace(_request(), approval_id="a-new", nonce="n-new", at=later, expires_at=later + HOLD))
    assert second.id == "a1"
    assert second.nonce == "n1"


def test_expiry_blocks_decide_and_a_later_open_is_new() -> None:
    book = ApprovalBook()
    _open(book)
    expired = book.expire(AT + HOLD)
    assert expired[0].phase is ApprovalPhase.EXPIRED_TO_NATIVE
    assert expired[0].by is DecisionBy.TIMEOUT
    with pytest.raises(DomainError, match="ALREADY_DECIDED"):
        _ = book.decide("a1", nonce="n1", decision=ApprovalDecision.DENY, remember_prefix=False, at=AT + HOLD)
    fresh = book.open(
        replace(
            _request(),
            approval_id="a2",
            nonce="n2",
            at=AT + HOLD,
            expires_at=AT + HOLD + HOLD,
            allow_tab_session=False,
        )
    )
    assert fresh.id == "a2"
    with pytest.raises(ValueError, match="tab_session"):
        _ = book.decide("a2", nonce="n2", decision=ApprovalDecision.ALLOW, remember_prefix=True, at=AT + HOLD)


def test_release_cancel_and_missing() -> None:
    book = ApprovalBook()
    _open(book)
    released = book.release("a1", nonce="n1", at=AT)
    assert released.phase is ApprovalPhase.EXPIRED_TO_NATIVE
    later = AT + timedelta(seconds=1)
    _open(
        book,
        replace(_request(), approval_id="a2", nonce="n2", input_sha256="other", at=later, expires_at=later + HOLD),
    )
    cancelled = book.cancel("a2", at=AT + timedelta(seconds=1))
    assert cancelled.phase is ApprovalPhase.CANCELLED
    with pytest.raises(DomainError, match="NOT_FOUND"):
        _ = book.get("missing")


class ApprovalMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.book: ApprovalBook = ApprovalBook()
        self.at: datetime = AT
        self.seq: int = 0
        # The approval ids in creation order. A duplicate input returns an id that is already here.
        self.ids: list[str] = []

    def _open(self, sha: str) -> None:
        self.seq += 1
        self.at += timedelta(seconds=1)
        created = self.book.open(
            replace(
                _request(),
                approval_id=f"id-{self.seq}",
                nonce=f"nonce-{self.seq}",
                command="rm build",
                input_sha256=sha,
                at=self.at,
                expires_at=self.at + HOLD,
            )
        )
        if created.id not in self.ids:
            self.ids.append(created.id)

    @rule(sha=st.sampled_from(("same", "other")))
    def open_request(self, sha: str) -> None:
        before = len(self.ids)
        self._open(sha)
        assert len(self.ids) >= before

    @rule(allow=st.booleans())
    def decide_latest(self, *, allow: bool) -> None:
        listed = [self.book.get(approval_id) for approval_id in self.ids]
        pending = [item for item in listed if item.phase is ApprovalPhase.PENDING]
        if not pending:
            return
        current = pending[-1]
        decision = ApprovalDecision.ALLOW if allow else ApprovalDecision.DENY
        updated = self.book.decide(
            current.id,
            nonce=current.nonce,
            decision=decision,
            remember_prefix=False,
            at=current.requested_at,
        )
        assert updated.phase in {ApprovalPhase.ALLOWED, ApprovalPhase.DENIED}
        with pytest.raises(DomainError, match="ALREADY_DECIDED"):
            _ = self.book.decide(
                current.id,
                nonce="nope",
                decision=decision,
                remember_prefix=False,
                at=current.requested_at,
            )
        assert self.book.get(current.id).phase is updated.phase

    @invariant()
    def terminal_phases_stay_terminal(self) -> None:
        for approval_id in self.ids:
            item = self.book.get(approval_id)
            if item.phase is not ApprovalPhase.PENDING:
                assert item.by is not None


TestApprovals = ApprovalMachine.TestCase
TestApprovals.settings = settings(max_examples=50, stateful_step_count=12, deadline=None)
