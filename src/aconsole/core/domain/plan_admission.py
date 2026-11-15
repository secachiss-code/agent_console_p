"""Admission of H-DAG contracts, including language containment of path globs."""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Protocol

from aconsole.core.domain.errors import DomainError

_MAX_CODEPOINT = 0x10FFFF
_DIRECTION_MIN = 5
_DIRECTION_MAX = 8
_MODULE_MIN = 15
_MODULE_MAX = 25


class NodeContract(Protocol):
    """Immutable contract surface; admission does not depend on the graph implementation."""

    @property
    def key(self) -> str: ...

    @property
    def level(self) -> int: ...

    @property
    def pipeline(self) -> bool: ...

    @property
    def paths(self) -> tuple[str, ...]: ...

    @property
    def invariants(self) -> str | None: ...

    @property
    def gate_profile(self) -> Mapping[str, object] | None: ...


@dataclass(frozen=True)
class _Edge:
    target: int
    label: str | None = None


class _Glob:
    """Small epsilon NFA. Paths carry a final slash so ** repeats whole segments."""

    def __init__(self, patterns: tuple[str, ...]) -> None:
        self.edges: list[list[_Edge]] = [[]]
        self.ends: set[int] = set()
        for pattern in patterns:
            state = self._state()
            self.edges[0].append(_Edge(state))
            for segment in pattern.split("/"):
                if segment == "**":
                    following = self._state()
                    body = self._state()
                    self.edges[state].extend((_Edge(following), _Edge(body, "?")))
                    self.edges[body].extend((_Edge(body, "?"), _Edge(state, "/")))
                    state = following
                else:
                    for pattern_piece in _segment_tokens(segment):
                        following = self._state()
                        if pattern_piece == "*":
                            self.edges[state].extend((_Edge(following), _Edge(state, "?")))
                        else:
                            self.edges[state].append(_Edge(following, pattern_piece))
                        state = following
                    following = self._state()
                    self.edges[state].append(_Edge(following, "/"))
                    state = following
            self.ends.add(state)

    def _state(self) -> int:
        self.edges.append([])
        return len(self.edges) - 1

    def closure(self, states: frozenset[int]) -> frozenset[int]:
        found = set(states)
        stack = list(states)
        while stack:
            for edge in self.edges[stack.pop()]:
                if edge.label is None and edge.target not in found:
                    found.add(edge.target)
                    stack.append(edge.target)
        return frozenset(found)

    def step(self, states: frozenset[int], char: str) -> frozenset[int]:
        return self.closure(
            frozenset(
                edge.target
                for state in states
                for edge in self.edges[state]
                if edge.label is not None
                and ((char == "/" and edge.label == "/") or (char != "/" and fnmatchcase(char, edge.label)))
            )
        )


def _segment_tokens(segment: str) -> tuple[str, ...]:
    tokens: list[str] = []
    index = 0
    while index < len(segment):
        end = index + 1
        if segment[index] == "[":
            if end < len(segment) and segment[end] == "!":
                end += 1
            if end < len(segment) and segment[end] == "]":
                end += 1
            while end < len(segment) and segment[end] != "]":
                end += 1
            end = end + 1 if end < len(segment) else index + 1
        tokens.append(segment[index:end])
        index = end
    return tuple(tokens)


def _alphabet(patterns: tuple[str, ...]) -> tuple[str, ...]:
    # Glob predicates change truth only at literal characters and class range boundaries.
    points = {0, ord("/"), ord("/") + 1}
    for pattern in patterns:
        for char in pattern:
            points.add(ord(char))
            if ord(char) < _MAX_CODEPOINT:
                points.add(ord(char) + 1)
    return tuple(chr(point) for point in sorted(points))


def _safe(pattern: str) -> bool:
    return (
        bool(pattern)
        and not pattern.startswith("/")
        and "\\" not in pattern
        and all(part not in {"", ".", ".."} for part in pattern.split("/"))
    )


def scope_contains(scope: tuple[str, ...], paths: tuple[str, ...]) -> bool:
    """Every path matched by the child must belong to the union of parent globs.

    Intersection is insufficient: src/*.py overlaps src/a.py but grants more access.
    Determinized NFA pairs find a counterexample to inclusion without sampling paths.
    """
    if not all(_safe(pattern) for pattern in (*scope, *paths)):
        return False
    child = _Glob(paths)
    parent = _Glob(scope)
    alphabet = _alphabet((*scope, *paths))
    start: tuple[frozenset[int], frozenset[int], bool] = (
        child.closure(frozenset({0})),
        parent.closure(frozenset({0})),
        False,
    )
    queue: deque[tuple[frozenset[int], frozenset[int], bool]] = deque([start])
    seen: set[tuple[frozenset[int], frozenset[int], bool]] = {start}
    while queue:
        left, right, in_segment = queue.popleft()
        if not in_segment and child.ends.intersection(left) and not parent.ends.intersection(right):
            return False
        for char in alphabet:
            if char == "/" and not in_segment:
                continue
            next_left = child.step(left, char)
            if not next_left:
                continue
            pair = (next_left, parent.step(right, char), char != "/")
            if pair not in seen:
                seen.add(pair)
                queue.append(pair)
    return True


def validate_contract(node: NodeContract, parent: NodeContract | None) -> None:
    """Legacy L1 nodes keep their existing parent semantics."""
    invalid = node.level not in {0, 1}
    if node.level == 0:
        invalid = invalid or parent is not None or node.pipeline
    elif node.pipeline:
        invalid = invalid or parent is None or parent.level != 0 or not node.paths
        if parent is not None:
            invalid = invalid or not scope_contains(parent.paths, node.paths)
    if node.level == 1:
        invalid = invalid or node.invariants is not None or node.gate_profile is not None
    if invalid:
        raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False, extra=(node.key,))


def admission_warnings(nodes: tuple[NodeContract, ...]) -> tuple[str, ...]:
    warnings: list[str] = []
    directions = sum(node.level == 0 for node in nodes)
    modules = sum(node.level == 1 for node in nodes)
    if not _DIRECTION_MIN <= directions <= _DIRECTION_MAX:
        warnings.append(f"L0_COUNT: {directions}; expected 5..8")
    if not _MODULE_MIN <= modules <= _MODULE_MAX:
        warnings.append(f"L1_COUNT: {modules}; expected 15..25")
    return tuple(warnings)
