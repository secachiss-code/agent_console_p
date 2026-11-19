"""Admission of H-DAG contracts, including language containment of path globs."""

import hashlib
import json
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from fnmatch import fnmatchcase
from typing import Literal, Protocol

from aconsole.core.domain.errors import DomainError

_MAX_CODEPOINT = 0x10FFFF
_DIRECTION_MIN = 5
_DIRECTION_MAX = 8
_MODULE_MIN = 15
_MODULE_MAX = 25
_PARENT_DEPTH_MAX = 3
_JSON_LOADS: Callable[[str], object] = json.loads


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


@dataclass(frozen=True, kw_only=True)
class TopologyContract:
    """Canonical, immutable contract input, independent of leases and node status.

    gate_profile_json is canonicalized when constructed so mutable adapter dictionaries
    cannot change a frozen plan. Application adapters serialize their L0 profile here.
    """

    key: str
    objective: str
    level: int = 1
    parent: str | None = None
    paths: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    test_cmd: str | None = None
    boundaries: str | None = None
    output_format: str | None = None
    invariants: str | None = None
    gate_profile_json: str = "null"

    def __post_init__(self) -> None:
        decoded = _JSON_LOADS(self.gate_profile_json)
        object.__setattr__(self, "gate_profile_json", _canonical(decoded).decode())


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def contract_sha256(node: TopologyContract, parent: TopologyContract | None) -> str:
    """The §4.3 payload; direction invariants and gates bind every child contract."""
    direction = node if node.level == 0 else parent
    return _hash(
        {
            "objective": node.objective,
            "acceptance": node.acceptance,
            "paths": node.paths,
            "test_cmd": node.test_cmd,
            "boundaries": node.boundaries,
            "output_format": node.output_format,
            "parent": node.parent,
            "invariants": None if direction is None else direction.invariants,
            "gate_profile": None if direction is None else json.loads(direction.gate_profile_json),
        }
    )


@dataclass(frozen=True, kw_only=True)
class AppliedChange:
    """A decision loaded by the application from plan_inbox, never from agent args.

    proof_receipt is supplied only after the application has resolved the blocker
    receipt from the audit store. The domain checks its presence for a split.
    """

    id: str
    kind: Literal["add", "split", "backlog"]
    nodes: tuple[TopologyContract, ...] = ()
    status: Literal["open", "applied", "rejected"] = "open"
    decided_by: str | None = None
    proof_receipt: str | None = None


@dataclass(frozen=True, kw_only=True)
class PlanTopology:
    """Domain admission policy. Graph status and backlog bodies live in their stores.

    Cancelled nodes remain in nodes: gross_added counts every insertion, never live
    work. All mutations return a new value, allowing adapters to commit atomically.
    """

    nodes: tuple[TopologyContract, ...] = ()
    phase: Literal["draft", "frozen", "completed"] = "draft"
    revision_no: int = 0
    frozen_at: datetime | None = None
    baseline_nodes: int = 0
    gross_added: int = 0
    baseline_children: tuple[tuple[str, int], ...] = ()
    contract_hashes: tuple[tuple[str, str], ...] = ()
    gates_sha256: str | None = None
    applied_changes: tuple[str, ...] = ()

    def add(self, nodes: tuple[TopologyContract, ...]) -> PlanTopology:
        if self.phase != "draft":
            raise _admission_error(4011, "PLAN_FROZEN")
        return self._insert(nodes)

    def freeze(self, *, at: datetime, decided_by: str) -> PlanTopology:
        _require_human(decided_by)
        if self.phase == "frozen":
            return self
        if self.phase != "draft":
            raise _admission_error(4011, "PLAN_FROZEN")
        _validate_topology(self.nodes)
        parents = {node.key: node for node in self.nodes}
        hashes = tuple(
            sorted(
                (node.key, contract_sha256(node, parents.get(node.parent) if node.parent is not None else None))
                for node in self.nodes
            )
        )
        gates = {node.key: json.loads(node.gate_profile_json) for node in self.nodes if node.level == 0}
        return replace(
            self,
            phase="frozen",
            revision_no=self.revision_no + 1,
            frozen_at=at,
            baseline_nodes=len(self.nodes),
            gross_added=max(self.gross_added, len(self.nodes)),
            baseline_children=_child_counts(self.nodes),
            contract_hashes=hashes,
            gates_sha256=_hash(gates),
        )

    def apply_change(self, change: AppliedChange) -> PlanTopology:
        _require_human(change.decided_by)
        if change.status != "applied" or not change.id or self.phase == "completed":
            raise _admission_error(4011, "PLAN_FROZEN")
        if change.kind == "backlog":
            if change.nodes:
                raise _admission_error(4013, "NODE_CONTRACT")
            return self
        if change.id in self.applied_changes:
            raise _admission_error(4011, "PLAN_FROZEN")
        if change.kind == "split" and not change.proof_receipt:
            raise _admission_error(4019, "SPLIT_NO_PROOF")
        updated = self._insert(change.nodes)
        return replace(updated, revision_no=updated.revision_no + 1, applied_changes=(*self.applied_changes, change.id))

    def complete(self, *, decided_by: str) -> PlanTopology:
        """Caller first verifies direction receipts and terminal graph statuses."""
        _require_human(decided_by)
        if self.phase == "draft":
            raise _admission_error(4011, "PLAN_FROZEN")
        return replace(self, phase="completed")

    def refresh_direction_profiles(self, *, keys: set[str]) -> PlanTopology:
        """Rebind amended L0 contracts while retaining the execution contracts of L1."""
        hashes = dict(self.contract_hashes)
        for node in self.nodes:
            if node.key in keys:
                hashes[node.key] = contract_sha256(node, None)
        gates = {node.key: json.loads(node.gate_profile_json) for node in self.nodes if node.level == 0}
        return replace(
            self,
            revision_no=self.revision_no + 1,
            contract_hashes=tuple(sorted(hashes.items())),
            gates_sha256=_hash(gates),
        )

    def _insert(self, fresh: tuple[TopologyContract, ...]) -> PlanTopology:
        nodes = (*self.nodes, *fresh)
        _validate_topology(nodes)
        gross = max(self.gross_added, len(self.nodes)) + len(fresh)
        if self.phase == "frozen":
            # Integer arithmetic avoids rounding 20% allowances up on small plans.
            if gross * 5 > self.baseline_nodes * 6:
                raise _admission_error(4014, "PLAN_GROWTH_LIMIT")
            baseline = dict(self.baseline_children)
            if any(count > baseline.get(key, 0) + 2 for key, count in _child_counts(nodes)):
                raise _admission_error(4014, "PLAN_GROWTH_LIMIT")
        parents = {node.key: node for node in nodes}
        hashes = self.contract_hashes
        if self.phase == "frozen":
            # A change request can add contracts but cannot silently replace frozen ones.
            hashes = tuple(
                sorted(
                    (
                        *hashes,
                        *(
                            (
                                node.key,
                                contract_sha256(node, parents.get(node.parent) if node.parent is not None else None),
                            )
                            for node in fresh
                        ),
                    )
                )
            )
        gates = self.gates_sha256
        if self.phase == "frozen":
            gates = _hash({node.key: json.loads(node.gate_profile_json) for node in nodes if node.level == 0})
        return replace(self, nodes=nodes, gross_added=gross, contract_hashes=hashes, gates_sha256=gates)


def _require_human(decided_by: str | None) -> None:
    if decided_by != "human":
        raise _admission_error(4012, "PLAN_ROLE")


def _admission_error(code: int, name: str) -> DomainError:
    return DomainError(code=code, name=name, retryable=False)


def _child_counts(nodes: tuple[TopologyContract, ...]) -> tuple[tuple[str, int], ...]:
    counts = dict.fromkeys((node.key for node in nodes), 0)
    for node in nodes:
        if node.parent is not None:
            counts[node.parent] += 1
    return tuple(sorted(counts.items()))


def _validate_topology(nodes: tuple[TopologyContract, ...]) -> None:
    by_key = {node.key: node for node in nodes}
    if len(by_key) != len(nodes):
        raise _admission_error(4013, "NODE_CONTRACT")
    for node in nodes:
        seen = {node.key}
        parent = node.parent
        depth = 1
        while parent is not None:
            if parent in seen:
                raise _admission_error(4003, "PLAN_CYCLE")
            if parent not in by_key:
                raise _admission_error(4013, "NODE_CONTRACT")
            seen.add(parent)
            parent = by_key[parent].parent
            depth += 1
        if depth > _PARENT_DEPTH_MAX:
            raise _admission_error(4014, "PLAN_GROWTH_LIMIT")
