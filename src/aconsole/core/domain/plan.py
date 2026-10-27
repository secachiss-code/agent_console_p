"""Plan DAG: status rules, leases, path overlap, priority, and layout."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from aconsole.core.domain.errors import DomainError

_LEASE = timedelta(minutes=15)
_NOT_FOUND = 4002
_CYCLE = 4003
_TRANSITION = 4004
_NOT_READY = 4005
_LEASE_HELD = 4006
_STALE_FENCE = 4007
_LEASE_EXPIRED = 4008
_EVIDENCE = 4009
_OVERLAP = 4010


class NodeStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VERIFYING = "verifying"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Role(StrEnum):
    AGENT = "agent"
    HUMAN = "human"


_LEASED = frozenset({NodeStatus.IN_PROGRESS, NodeStatus.VERIFYING})
_DONE_LIKE = frozenset({NodeStatus.DONE, NodeStatus.CANCELLED})


@dataclass(frozen=True, kw_only=True)
class _Rule:
    roles: frozenset[Role]
    evidence: bool = False
    note: bool = False
    claim: bool = False
    review_blocks: bool = False


def _both(*, evidence: bool = False, note: bool = False, claim: bool = False, review_blocks: bool = False) -> _Rule:
    return _Rule(
        roles=frozenset({Role.AGENT, Role.HUMAN}),
        evidence=evidence,
        note=note,
        claim=claim,
        review_blocks=review_blocks,
    )


def _human() -> _Rule:
    return _Rule(roles=frozenset({Role.HUMAN}))


def human_transition_issue(source: NodeStatus, target: NodeStatus, *, review_required: bool) -> str | None:
    """Why a human vault edit cannot set this status, or None when the edge is legal.

    Evidence and note stay on the agent transition. A frontmatter edit cannot supply them.
    """
    if source is target:
        return None
    rule = _RULES.get((source, target))
    if rule is None or Role.HUMAN not in rule.roles or (rule.review_blocks and review_required):
        return "NODE_TRANSITION_INVALID"
    if rule.evidence or rule.note:
        return "NODE_EVIDENCE_REQUIRED"
    return None


# Missing pairs are illegal. The diagonal is illegal too.
_RULES: dict[tuple[NodeStatus, NodeStatus], _Rule] = {
    (NodeStatus.PENDING, NodeStatus.IN_PROGRESS): _both(claim=True),
    (NodeStatus.PENDING, NodeStatus.BLOCKED): _both(),
    (NodeStatus.PENDING, NodeStatus.CANCELLED): _human(),
    (NodeStatus.IN_PROGRESS, NodeStatus.PENDING): _both(),
    (NodeStatus.IN_PROGRESS, NodeStatus.VERIFYING): _both(evidence=True),
    (NodeStatus.IN_PROGRESS, NodeStatus.BLOCKED): _both(note=True),
    (NodeStatus.IN_PROGRESS, NodeStatus.DONE): _both(evidence=True, review_blocks=True),
    (NodeStatus.IN_PROGRESS, NodeStatus.FAILED): _both(note=True),
    (NodeStatus.IN_PROGRESS, NodeStatus.CANCELLED): _human(),
    (NodeStatus.VERIFYING, NodeStatus.IN_PROGRESS): _both(),
    (NodeStatus.VERIFYING, NodeStatus.DONE): _human(),
    (NodeStatus.VERIFYING, NodeStatus.FAILED): _both(),
    (NodeStatus.VERIFYING, NodeStatus.CANCELLED): _human(),
    (NodeStatus.BLOCKED, NodeStatus.PENDING): _both(),
    (NodeStatus.BLOCKED, NodeStatus.CANCELLED): _human(),
    (NodeStatus.FAILED, NodeStatus.PENDING): _both(),
    (NodeStatus.FAILED, NodeStatus.CANCELLED): _human(),
    (NodeStatus.DONE, NodeStatus.PENDING): _human(),
    (NodeStatus.CANCELLED, NodeStatus.PENDING): _human(),
}


@dataclass(frozen=True, kw_only=True)
class NodeSpec:
    key: str
    created_at: datetime
    depends_on: tuple[str, ...] = ()
    parent: str | None = None
    related: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    review_required: bool = False
    user_moved: bool = False
    pin: int | None = None


@dataclass(frozen=True, kw_only=True)
class Node:
    key: str
    created_at: datetime
    status: NodeStatus = NodeStatus.PENDING
    paths: tuple[str, ...] = ()
    review_required: bool = False
    user_moved: bool = False
    pin: int | None = None
    tab_id: str | None = None
    lease_id: str | None = None
    fence: int = 0
    lease_expires_at: datetime | None = None
    worktree_id: str | None = None
    evidence: str | None = None
    note: str | None = None


@dataclass(frozen=True, kw_only=True)
class Move:
    key: str
    to: NodeStatus
    role: Role
    at: datetime
    tab_id: str | None = None
    fence: int | None = None
    evidence: str | None = None
    note: str | None = None


def _fail(code: int, name: str, extra: tuple[str, ...] = ()) -> DomainError:
    retryable = code in {_LEASE_HELD, _OVERLAP}
    return DomainError(code=code, name=name, retryable=retryable, extra=extra)


@dataclass(frozen=True, slots=True)
class _CharSet:
    """One glob position: a set of closed code-point ranges, or its complement."""

    ranges: tuple[tuple[int, int], ...]
    negated: bool


@dataclass(frozen=True, slots=True)
class _Token:
    chars: _CharSet
    star: bool


_ANY = _CharSet(ranges=(), negated=True)


def _class_token(pattern: str, start: int) -> tuple[_CharSet, int] | None:
    """Parse ``[...]`` at ``start`` the way fnmatch does; None when it is not closed."""
    index = start + 1
    negated = index < len(pattern) and pattern[index] in "!^"
    if negated:
        index += 1
    ranges: list[tuple[int, int]] = []
    first = True
    while index < len(pattern) and (first or pattern[index] != "]"):
        first = False
        low = pattern[index]
        if index + 2 < len(pattern) and pattern[index + 1] == "-" and pattern[index + 2] != "]":
            high = pattern[index + 2]
            if ord(low) <= ord(high):
                ranges.append((ord(low), ord(high)))
            index += 3
        else:
            ranges.append((ord(low), ord(low)))
            index += 1
    if index >= len(pattern):
        return None
    return _CharSet(ranges=tuple(ranges), negated=negated), index + 1


def _tokens(segment: str) -> tuple[_Token, ...]:
    tokens: list[_Token] = []
    index = 0
    while index < len(segment):
        char = segment[index]
        if char == "*":
            if not (tokens and tokens[-1].star):
                tokens.append(_Token(chars=_ANY, star=True))
            index += 1
        elif char == "?":
            tokens.append(_Token(chars=_ANY, star=False))
            index += 1
        elif char == "[" and (parsed := _class_token(segment, index)) is not None:
            charset, index = parsed
            tokens.append(_Token(chars=charset, star=False))
        else:
            tokens.append(_Token(chars=_CharSet(ranges=((ord(char), ord(char)),), negated=False), star=False))
            index += 1
    return tuple(tokens)


def _covered(low: int, high: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    """True when every code point in [low, high] is inside ``ranges``."""
    point = low
    for start, end in sorted(ranges):
        if start > point:
            return False
        point = max(point, end + 1)
        if point > high:
            return True
    return point > high


def _chars_meet(left: _CharSet, right: _CharSet) -> bool:
    """True when some character belongs to both sets (the alphabet is unbounded)."""
    if left.negated and right.negated:
        return True
    if left.negated:
        return any(not _covered(low, high, left.ranges) for low, high in right.ranges)
    if right.negated:
        return any(not _covered(low, high, right.ranges) for low, high in left.ranges)
    return any(a_low <= b_high and b_low <= a_high for a_low, a_high in left.ranges for b_low, b_high in right.ranges)


def _segment_overlap(left: str, right: str) -> bool:
    """True when one path segment can match both fnmatch patterns.

    Product of the two pattern automata: ``*`` may skip or consume a character,
    other tokens consume exactly one character that both sides accept.
    """
    a = _tokens(left)
    b = _tokens(right)
    seen: set[tuple[int, int]] = set()
    stack = [(0, 0)]
    while stack:
        i, j = stack.pop()
        if (i, j) in seen:
            continue
        seen.add((i, j))
        if i == len(a) and j == len(b):
            return True
        if i < len(a) and a[i].star:
            stack.append((i + 1, j))
        if j < len(b) and b[j].star:
            stack.append((i, j + 1))
        if i < len(a) and j < len(b) and not (a[i].star and b[j].star) and _chars_meet(a[i].chars, b[j].chars):
            stack.append((i if a[i].star else i + 1, j if b[j].star else j + 1))
    return False


def _parts(pattern: str) -> tuple[str, ...]:
    return tuple(part for part in pattern.split("/") if part != "")


def globs_overlap(left: str, right: str) -> bool:
    """True when some path can match both glob patterns."""
    a = _parts(left)
    b = _parts(right)
    memo: dict[tuple[int, int], bool] = {}

    def walk(i: int, j: int) -> bool:
        cached = memo.get((i, j))
        if cached is not None:
            return cached
        if i == len(a) and j == len(b):
            memo[(i, j)] = True
            return True
        if i < len(a) and a[i] == "**":
            hit = walk(i + 1, j) or (j < len(b) and walk(i, j + 1))
            memo[(i, j)] = hit
            return hit
        if j < len(b) and b[j] == "**":
            hit = walk(i, j + 1) or (i < len(a) and walk(i + 1, j))
            memo[(i, j)] = hit
            return hit
        hit = i < len(a) and j < len(b) and _segment_overlap(a[i], b[j]) and walk(i + 1, j + 1)
        memo[(i, j)] = hit
        return hit

    return walk(0, 0)


def paths_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return any(globs_overlap(a, b) for a in left for b in right)


class Plan:
    """One plan. Blocking edges are a DAG. A node has at most one lease holder."""

    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._blockers: dict[str, tuple[str, ...]] = {}
        self._parents: dict[str, str | None] = {}
        self._related: dict[str, tuple[str, ...]] = {}

    def add(self, spec: NodeSpec) -> Node:
        if spec.key in self._nodes:
            message = f"duplicate node {spec.key}"
            raise ValueError(message)
        self._require_known(spec.depends_on)
        if spec.parent is not None:
            self._require_known((spec.parent,))
        self._require_known(spec.related)
        self._nodes[spec.key] = Node(
            key=spec.key,
            created_at=spec.created_at,
            paths=spec.paths,
            review_required=spec.review_required,
            user_moved=spec.user_moved,
            pin=spec.pin,
        )
        self._blockers[spec.key] = spec.depends_on
        self._parents[spec.key] = spec.parent
        self._related[spec.key] = spec.related
        cycle = self.cycle_path()
        if cycle is not None:
            self._drop(spec.key)
            raise _fail(_CYCLE, "PLAN_CYCLE", cycle)
        return self._nodes[spec.key]

    def depend(self, key: str, on: str) -> None:
        _ = self.get(key)
        _ = self.get(on)
        current = self._blockers[key]
        if on in current:
            return
        self._blockers[key] = (*current, on)
        cycle = self.cycle_path()
        if cycle is not None:
            self._blockers[key] = current
            raise _fail(_CYCLE, "PLAN_CYCLE", cycle)

    def get(self, key: str) -> Node:
        found = self._nodes.get(key)
        if found is None:
            raise _fail(_NOT_FOUND, "NODE_NOT_FOUND", (key,))
        return found

    def unmet_blockers(self, key: str) -> tuple[str, ...]:
        node = self.get(key)
        return tuple(dep for dep in self._blockers[node.key] if self._nodes[dep].status is not NodeStatus.DONE)

    def ready(self, key: str) -> bool:
        node = self.get(key)
        return node.status is NodeStatus.PENDING and not self.unmet_blockers(key)

    def priority(self, key: str, now: datetime) -> float:
        node = self.get(key)
        age_days = max(0.0, (now - node.created_at).total_seconds() / 86400)
        return float(self._longest(key)) + 0.1 * float(self._fanout(key)) + 0.01 * age_days

    def transition(self, move: Move) -> Node:
        node = self.get(move.key)
        self._expire_if_due(node, move.at)
        node = self.get(move.key)
        self._check_rule(node, move)
        if node.lease_expires_at is not None:
            self._require_fence(node, move.fence, move.at)
        elif move.fence is not None:
            # Any fence is stale once the lease that issued it expired or was released. A move without a fence is
            # checked by the transition rules alone, as for a node that was never leased.
            raise _fail(_STALE_FENCE, "LEASE_STALE_FENCE", (str(node.fence),))
        if move.to is NodeStatus.IN_PROGRESS and node.status is not NodeStatus.IN_PROGRESS:
            self._check_claim(node, move)
        updated = self._apply(node, move)
        self._nodes[node.key] = updated
        return updated

    def claim(self, key: str, *, tab_id: str, at: datetime, role: Role = Role.AGENT) -> Node:
        node = self.get(key)
        self._expire_if_due(node, at)
        node = self.get(key)
        if node.tab_id is not None and node.tab_id != tab_id and node.status in _LEASED:
            raise _fail(_LEASE_HELD, "LEASE_HELD", (node.tab_id,))
        return self.transition(Move(key=key, to=NodeStatus.IN_PROGRESS, role=role, at=at, tab_id=tab_id))

    def heartbeat(self, key: str, *, fence: int, at: datetime) -> Node:
        node = self.get(key)
        self._require_fence(node, fence, at)
        updated = replace(node, lease_expires_at=at + _LEASE)
        self._nodes[key] = updated
        return updated

    def expire_leases(self, at: datetime) -> tuple[str, ...]:
        expired: list[str] = []
        for key in tuple(self._nodes):
            node = self._nodes[key]
            if node.lease_expires_at is not None and at >= node.lease_expires_at and node.status in _LEASED:
                self._nodes[key] = self._clear_holder(node, NodeStatus.PENDING)
                expired.append(key)
        return tuple(expired)

    def kahn(self) -> tuple[str, ...] | None:
        indegree = self._zero()
        following: dict[str, list[str]] = {key: [] for key in self._nodes}
        for key, blockers in self._blockers.items():
            indegree[key] = len(blockers)
            for blocker in blockers:
                following[blocker].append(key)
        ready = sorted(key for key, degree in indegree.items() if degree == 0)
        order: list[str] = []
        while ready:
            key = ready.pop(0)
            order.append(key)
            for nxt in sorted(following[key]):
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    ready.append(nxt)
                    ready.sort()
        if len(order) != len(self._nodes):
            return None
        return tuple(order)

    def cycle_path(self) -> tuple[str, ...] | None:
        color = self._zero()
        stack: list[str] = []

        def visit(key: str) -> tuple[str, ...] | None:
            color[key] = 1
            stack.append(key)
            found: tuple[str, ...] | None = None
            for nxt in self._dependents(key):
                if color[nxt] == 1:
                    start = stack.index(nxt)
                    found = (*stack[start:], nxt)
                    break
                if color[nxt] == 0:
                    found = visit(nxt)
                    if found is not None:
                        break
            _ = stack.pop()
            color[key] = 2
            return found

        for key in sorted(self._nodes):
            if color[key] != 0:
                continue
            found = visit(key)
            if found is not None:
                return found
        return None

    def layout(self) -> tuple[tuple[str, int, int], ...]:
        grouped = self._groups()
        ordered = {layer: self._seed(keys) for layer, keys in grouped.items()}
        ordered = self._pass(ordered, downstream=False)
        ordered = self._pass(ordered, downstream=True)
        rows: list[tuple[str, int, int]] = []
        for layer in sorted(ordered):
            for index, key in enumerate(ordered[layer]):
                rows.append((key, layer, index))
        return tuple(rows)

    def _drop(self, key: str) -> None:
        _ = self._nodes.pop(key, None)
        _ = self._blockers.pop(key, None)
        _ = self._parents.pop(key, None)
        _ = self._related.pop(key, None)

    def _zero(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for key in self._nodes:
            counts[key] = 0
        return counts

    def _require_known(self, keys: tuple[str, ...]) -> None:
        for key in keys:
            if key not in self._nodes:
                raise _fail(_NOT_FOUND, "NODE_NOT_FOUND", (key,))

    def _dependents(self, key: str) -> tuple[str, ...]:
        return tuple(sorted(node for node, blockers in self._blockers.items() if key in blockers))

    def _check_rule(self, node: Node, move: Move) -> None:
        rule = _RULES.get((node.status, move.to))
        review = bool(rule is not None and rule.review_blocks and node.review_required)
        if rule is None or move.role not in rule.roles or review:
            allowed = self._allowed(node, move.role)
            raise _fail(_TRANSITION, "NODE_TRANSITION_INVALID", tuple(sorted(item.value for item in allowed)))
        if rule.claim and self.unmet_blockers(node.key):
            raise _fail(_NOT_READY, "NODE_NOT_READY", self.unmet_blockers(node.key))
        if rule.evidence and not move.evidence:
            raise _fail(_EVIDENCE, "NODE_EVIDENCE_REQUIRED")
        if rule.note and not move.note:
            raise _fail(_EVIDENCE, "NODE_EVIDENCE_REQUIRED", ("note",))

    def _allowed(self, node: Node, role: Role) -> frozenset[NodeStatus]:
        found: set[NodeStatus] = set()
        for (source, target), rule in _RULES.items():
            if source is not node.status or role not in rule.roles:
                continue
            if rule.review_blocks and node.review_required:
                continue
            found.add(target)
        return frozenset(found)

    def _check_claim(self, node: Node, move: Move) -> None:
        if move.tab_id is None:
            message = "claim requires tab_id"
            raise ValueError(message)
        for other in self._nodes.values():
            if other.key == node.key or other.status not in _LEASED:
                continue
            if other.tab_id == move.tab_id or not paths_overlap(node.paths, other.paths):
                continue
            raise _fail(_OVERLAP, "PATHS_OVERLAP", (other.key,))

    def _require_fence(self, node: Node, fence: int | None, at: datetime) -> None:
        if node.lease_expires_at is None or at >= node.lease_expires_at:
            self._expire_if_due(node, at)
            raise _fail(_LEASE_EXPIRED, "LEASE_EXPIRED")
        if fence != node.fence:
            raise _fail(_STALE_FENCE, "LEASE_STALE_FENCE", (str(node.fence),))

    def _expire_if_due(self, node: Node, at: datetime) -> None:
        if node.lease_expires_at is None or at < node.lease_expires_at or node.status not in _LEASED:
            return
        self._nodes[node.key] = self._clear_holder(node, NodeStatus.PENDING)

    def _apply(self, node: Node, move: Move) -> Node:
        evidence = move.evidence if move.evidence is not None else node.evidence
        note = move.note if move.note is not None else node.note
        if move.to is NodeStatus.IN_PROGRESS and node.status is not NodeStatus.IN_PROGRESS:
            fence = node.fence + 1
            return replace(
                node,
                status=move.to,
                fence=fence,
                tab_id=move.tab_id,
                lease_id=f"{node.key}:{fence}",
                lease_expires_at=move.at + _LEASE,
                worktree_id=node.worktree_id or move.tab_id,
                evidence=evidence,
                note=note,
            )
        if move.to in _LEASED:
            return replace(node, status=move.to, lease_expires_at=move.at + _LEASE, evidence=evidence, note=note)
        return replace(
            self._clear_holder(node, move.to),
            evidence=evidence,
            note=note,
        )

    def _clear_holder(self, node: Node, status: NodeStatus) -> Node:
        return replace(node, status=status, tab_id=None, lease_id=None, lease_expires_at=None)

    def _fanout(self, key: str) -> int:
        return len(self._dependents(key))

    def _longest(self, key: str) -> int:
        memo: dict[str, int] = {}

        def walk(node_key: str) -> int:
            if node_key in memo:
                return memo[node_key]
            if self._nodes[node_key].status in _DONE_LIKE:
                memo[node_key] = 0
                return 0
            rest = [dep for dep in self._dependents(node_key) if self._nodes[dep].status not in _DONE_LIKE]
            best = 0 if not rest else 1 + max(walk(dep) for dep in rest)
            memo[node_key] = best
            return best

        return walk(key)

    def _groups(self) -> dict[int, list[str]]:
        layer = self._zero()
        for key in self.kahn() or sorted(self._nodes):
            blockers = [layer[dep] for dep in self._blockers[key] if dep in layer]
            layer[key] = 0 if not blockers else 1 + max(blockers)
        grouped: dict[int, list[str]] = {}
        for key, index in layer.items():
            grouped.setdefault(index, []).append(key)
        return grouped

    def _seed(self, keys: list[str]) -> list[str]:
        pinned = sorted(
            (key for key in keys if self._nodes[key].user_moved),
            key=lambda key: (self._nodes[key].pin if self._nodes[key].pin is not None else 0, key),
        )
        free = sorted(key for key in keys if key not in pinned)
        slots: list[str | None] = [None] * len(keys)
        for key in pinned:
            stored = self._nodes[key].pin
            pin = 0 if stored is None else stored
            index = min(max(pin, 0), len(keys) - 1)
            while slots[index] is not None:
                index = (index + 1) % len(keys)
            slots[index] = key
        cursor = 0
        for index, occupant in enumerate(slots):
            if occupant is None:
                slots[index] = free[cursor]
                cursor += 1
        return [key for key in slots if key is not None]

    def _pass(self, ordered: dict[int, list[str]], *, downstream: bool) -> dict[int, list[str]]:
        updated: dict[int, list[str]] = {}
        index_of = {key: index for keys in ordered.values() for index, key in enumerate(keys)}
        for layer, keys in ordered.items():
            score = {key: self._barycenter(key, index_of, downstream=downstream) for key in keys}
            updated[layer] = self._reorder(keys, score)
        return updated

    def _barycenter(self, key: str, index_of: dict[str, int], *, downstream: bool) -> float:
        neighbors = self._dependents(key) if downstream else self._blockers[key]
        indexes = [index_of[item] for item in neighbors if item in index_of]
        if not indexes:
            return 0.0
        return sum(indexes) / len(indexes)

    def _reorder(self, current: list[str], score: dict[str, float]) -> list[str]:
        pinned = {index: key for index, key in enumerate(current) if self._nodes[key].user_moved}
        movable = [key for key in current if not self._nodes[key].user_moved]
        movable.sort(key=lambda key: (score.get(key, 0.0), current.index(key)))
        result: list[str] = []
        cursor = 0
        for index in range(len(current)):
            held = pinned.get(index)
            if held is not None:
                result.append(held)
                continue
            result.append(movable[cursor])
            cursor += 1
        return result
