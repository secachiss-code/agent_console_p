"""Approval lifecycle: nonce, input hash, dedup, and exact command-prefix rules."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.prefix_allowlist import AllowEntry, allowed_by_entry, entry_for_prefix
from aconsole.core.domain.risk import RiskTag, classify
from aconsole.core.ports.paths import PathResolver

_NOT_FOUND = 3001
_EXPIRED = 3002
_NONCE = 3003
_DECIDED = 3004


class ApprovalPhase(StrEnum):
    PENDING = "pending"
    ALLOWED = "allowed"
    DENIED = "denied"
    EXPIRED_TO_NATIVE = "expired_to_native"
    CANCELLED = "cancelled"


class ApprovalDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class DecisionBy(StrEnum):
    USER = "user"
    RULE = "rule"
    TIMEOUT = "timeout"


def digest_input(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


_UNSAFE_CHARS = frozenset("$`{}*?[]~();&|<>\\'\"\n\r\t")
# The blanks that the shell splits on, besides newline, which `_UNSAFE_CHARS` refuses.
_BLANKS = " \t"


def _is_plain(text: str) -> bool:
    """True when `text` holds no character that expands, quotes, globs, or starts another command.

    The unsafe characters are: dollar, backtick, braces, asterisk, question mark, brackets, tilde,
    parentheses, semicolon, ampersand, pipe, angle brackets, backslash, single quote, double quote,
    newline, carriage return and tab. Only the space separates words. The shell would split or expand
    such text differently from the words shown in the approval, so it is covered only by an exact match.
    """
    return _UNSAFE_CHARS.isdisjoint(text)


@dataclass(frozen=True, kw_only=True)
class PathScope:
    """Where a command runs: its working directory, the worktree that bounds it, and the resolver that follows links.

    The default scope has no resolver, so a command with a path word is refused under it. Empty `cwd` and
    `worktree` make every absolute path count as outside the worktree, which fails closed.
    """

    cwd: str = ""
    worktree: str = ""
    resolver: PathResolver | None = None


def _within(path: str, worktree: str) -> bool:
    """True when `path` is `worktree` or lies below it. The separator is required, so `/wt2` is not inside `/wt`."""
    if worktree == "":
        return False
    return path == worktree or path.startswith(worktree.rstrip("/") + "/")


def _path_words(words: tuple[str, ...], entry: AllowEntry) -> tuple[str, ...]:
    """The words after the program and subcommand that are not flags. Every word after a `--` counts."""
    rest = words[1:] if entry.subcommand is None else words[2:]
    paths: list[str] = []
    ends_flags = False
    for word in rest:
        if ends_flags or not word.startswith("-"):
            paths.append(word)
        ends_flags = ends_flags or word == "--"
    return tuple(paths)


def _paths_inside(paths: tuple[str, ...], scope: PathScope) -> bool:
    """True when every path word resolves inside the worktree. Without a resolver or a cwd, any path word fails.

    The worktree is resolved too, so a worktree that is a symlink still bounds the paths that resolve into its target.
    """
    if not paths:
        return True
    if scope.resolver is None or scope.cwd == "" or scope.worktree == "":
        return False
    root = scope.resolver.resolve(scope.worktree, scope.cwd)
    return all(_within(scope.resolver.resolve(word, scope.cwd), root) for word in paths)


def _exact_paths_inside(text: str, scope: PathScope) -> bool:
    """An identical command is covered again only while its path words still resolve inside the worktree.

    A remembered rule may name a file that was a link inside the worktree when approved and points outside it now.
    """
    words = tuple(word for word in text.split(" ") if word)
    entry = entry_for_prefix(words[:2]) or entry_for_prefix(words[:1])
    return entry is None or _paths_inside(_path_words(words, entry), scope)


def prefix_matches(command: str, prefix: str, *, exact_only: bool = False, scope: PathScope | None = None) -> bool:
    """True when the remembered `prefix` covers `command`, compared as raw text with the ends stripped.

    Only ASCII spaces and tabs are blanks, as in the shell, so a no-break space stays part of its word.
    The identical command is covered whatever its risk, but when the scope is given and the command is an allowlisted
    program, its path words must still resolve inside the worktree. Unless `exact_only`, the prefix covers the
    command only when the prefix is exactly the program and subcommand of one `prefix_allowlist` entry (a bare
    `git` is not one), the command and the prefix are plain (see `_is_plain`), `allowed_by_entry` accepts the
    command's words, every path word resolves through the scope's resolver to a path inside its worktree, and
    `classify` finds no risk tag in the command. Every other prefix is exact-only. A path word is any word that is
    not a flag, the program or the subcommand. See `PathScope` for the empty and missing values, which fail closed.
    """
    rule = prefix.strip(_BLANKS)
    text = command.strip(_BLANKS)
    if rule == "":
        return False
    if text == rule:
        return scope is None or _exact_paths_inside(text, scope)
    if exact_only or not _is_plain(rule) or not _is_plain(text) or not text.startswith(rule + " "):
        return False
    entry = entry_for_prefix(tuple(rule.split(" ")))
    words = tuple(word for word in text.split(" ") if word)
    if entry is None or not allowed_by_entry(entry, words):
        return False
    boundary = PathScope() if scope is None else scope
    if not _paths_inside(_path_words(words, entry), boundary):
        return False
    return not classify(text, cwd=boundary.cwd, worktree=boundary.worktree)


@dataclass(frozen=True, kw_only=True)
class TabAllowRule:
    tab_id: str
    tool: str
    prefix: str


@dataclass(frozen=True, kw_only=True)
class TabDenyRule:
    tab_id: str
    tool: str
    prefix: str


def deny_matches(command: str, prefix: str) -> bool:
    """True when the remembered deny `prefix` covers `command`: the same text, or the prefix and then a blank.

    A deny rule needs no allowlist: covering more commands only asks the agent to stop sooner.
    """
    rule = prefix.strip(_BLANKS)
    text = command.strip(_BLANKS)
    if rule == "":
        return False
    return text == rule or (text.startswith(rule) and text[len(rule)] in _BLANKS)


@dataclass(frozen=True, kw_only=True)
class OpenRequest:
    approval_id: str
    nonce: str
    tab_id: str
    session_id: str
    tool: str
    command: str
    input_sha256: str
    risk: frozenset[RiskTag]
    at: datetime
    expires_at: datetime
    allow_tab_session: bool


@dataclass(frozen=True, kw_only=True)
class Approval:
    id: str
    nonce: str
    tab_id: str
    session_id: str
    tool: str
    command: str
    input_sha256: str
    risk: frozenset[RiskTag]
    requested_at: datetime
    expires_at: datetime
    phase: ApprovalPhase
    by: DecisionBy | None
    allow_tab_session: bool


def _conflict(code: int, name: str) -> DomainError:
    return DomainError(code=code, name=name, retryable=False)


class ApprovalBook:
    def __init__(self, *, cwd: str = "", worktree: str = "", resolver: PathResolver | None = None) -> None:
        self._items: dict[str, Approval] = {}
        self._by_input: dict[tuple[str, str, str], str] = {}
        self._rules: list[TabAllowRule] = []
        self._deny_rules: list[TabDenyRule] = []
        # Empty values make every absolute path count as outside the worktree. Without a resolver, a prefix rule
        # never covers a command with a path word, so a symbolic link is never followed to a place outside.
        self._scope: PathScope = PathScope(cwd=cwd, worktree=worktree, resolver=resolver)

    def get(self, approval_id: str) -> Approval:
        found = self._items.get(approval_id)
        if found is None:
            raise _conflict(_NOT_FOUND, "APPROVAL_NOT_FOUND")
        return found

    def open(self, request: OpenRequest) -> Approval:
        if request.expires_at < request.at:
            message = "expires_at must not precede the request"
            raise ValueError(message)
        key = (request.session_id, request.tool, request.input_sha256)
        existing_id = self._by_input.get(key)
        if existing_id is not None:
            existing = self._items[existing_id]
            if request.at < existing.expires_at and existing.phase is not ApprovalPhase.CANCELLED:
                return existing
        risk = request.risk | classify(request.command, cwd=self._scope.cwd, worktree=self._scope.worktree)
        phase = ApprovalPhase.PENDING
        by = None
        # A deny rule is checked first, so it wins over any allow rule that also covers the command.
        if self._deny_rule_for(request.tab_id, request.tool, request.command) is not None:
            phase = ApprovalPhase.DENIED
            by = DecisionBy.RULE
        elif self._rule_for(request.tab_id, request.tool, request.command, exact_only=bool(request.risk)) is not None:
            phase = ApprovalPhase.ALLOWED
            by = DecisionBy.RULE
        created = Approval(
            id=request.approval_id,
            nonce=request.nonce,
            tab_id=request.tab_id,
            session_id=request.session_id,
            tool=request.tool,
            command=request.command,
            input_sha256=request.input_sha256,
            risk=risk,
            requested_at=request.at,
            expires_at=request.expires_at,
            phase=phase,
            by=by,
            allow_tab_session=request.allow_tab_session,
        )
        self._items[request.approval_id] = created
        self._by_input[key] = request.approval_id
        return created

    def decide(
        self,
        approval_id: str,
        *,
        nonce: str,
        decision: ApprovalDecision,
        remember_prefix: bool,
        at: datetime,
    ) -> Approval:
        current = self._live(approval_id, nonce, at)
        if remember_prefix and not current.allow_tab_session:
            message = "tab_session is not offered for this approval"
            raise ValueError(message)
        phase = ApprovalPhase.ALLOWED if decision is ApprovalDecision.ALLOW else ApprovalPhase.DENIED
        updated = self._replace(current, phase=phase, by=DecisionBy.USER)
        if remember_prefix and decision is ApprovalDecision.ALLOW:
            self._rules.append(TabAllowRule(tab_id=current.tab_id, tool=current.tool, prefix=current.command))
        if remember_prefix and decision is ApprovalDecision.DENY:
            self._deny_rules.append(TabDenyRule(tab_id=current.tab_id, tool=current.tool, prefix=current.command))
        return updated

    def release(self, approval_id: str, *, nonce: str, at: datetime) -> Approval:
        current = self._live(approval_id, nonce, at)
        return self._replace(current, phase=ApprovalPhase.EXPIRED_TO_NATIVE, by=DecisionBy.USER)

    def release_pending(self) -> tuple[Approval, ...]:
        """Return every pending request to the agent's native prompt."""
        pending = tuple(item for item in self._items.values() if item.phase is ApprovalPhase.PENDING)
        return tuple(
            self._replace(item, phase=ApprovalPhase.EXPIRED_TO_NATIVE, by=DecisionBy.TIMEOUT) for item in pending
        )

    def expire(self, at: datetime) -> tuple[Approval, ...]:
        due = [item for item in self._items.values() if item.phase is ApprovalPhase.PENDING and at >= item.expires_at]
        return tuple(self._replace(item, phase=ApprovalPhase.EXPIRED_TO_NATIVE, by=DecisionBy.TIMEOUT) for item in due)

    def cancel(self, approval_id: str, *, at: datetime) -> Approval:
        current = self.get(approval_id)
        if current.phase is not ApprovalPhase.PENDING:
            raise _conflict(_DECIDED, "APPROVAL_ALREADY_DECIDED")
        if at >= current.expires_at:
            raise _conflict(_EXPIRED, "APPROVAL_EXPIRED")
        return self._replace(current, phase=ApprovalPhase.CANCELLED, by=DecisionBy.USER)

    def _live(self, approval_id: str, nonce: str, at: datetime) -> Approval:
        current = self.get(approval_id)
        if current.phase is not ApprovalPhase.PENDING:
            raise _conflict(_DECIDED, "APPROVAL_ALREADY_DECIDED")
        if at >= current.expires_at:
            raise _conflict(_EXPIRED, "APPROVAL_EXPIRED")
        if nonce != current.nonce:
            raise _conflict(_NONCE, "APPROVAL_NONCE_MISMATCH")
        return current

    def _replace(self, current: Approval, *, phase: ApprovalPhase, by: DecisionBy) -> Approval:
        updated = Approval(
            id=current.id,
            nonce=current.nonce,
            tab_id=current.tab_id,
            session_id=current.session_id,
            tool=current.tool,
            command=current.command,
            input_sha256=current.input_sha256,
            risk=current.risk,
            requested_at=current.requested_at,
            expires_at=current.expires_at,
            phase=phase,
            by=by,
            allow_tab_session=current.allow_tab_session,
        )
        self._items[current.id] = updated
        return updated

    def _deny_rule_for(self, tab_id: str, tool: str, command: str) -> TabDenyRule | None:
        for rule in self._deny_rules:
            if rule.tab_id == tab_id and rule.tool == tool and deny_matches(command, rule.prefix):
                return rule
        return None

    def _rule_for(self, tab_id: str, tool: str, command: str, *, exact_only: bool) -> TabAllowRule | None:
        """The first remembered rule that covers `command`. `exact_only` is set when the request carries a risk tag."""
        for rule in self._rules:
            if (
                rule.tab_id == tab_id
                and rule.tool == tool
                and prefix_matches(command, rule.prefix, exact_only=exact_only, scope=self._scope)
            ):
                return rule
        return None
