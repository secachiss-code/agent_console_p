"""Context stability, native session isolation, byte budgets and safe AST reduction."""

import ast
import json
from dataclasses import replace

import pytest

from aconsole.core.application.services.context_shaper import (
    ContextSession,
    ContextShaper,
    DirectionContext,
    LeaseTail,
    ModuleContext,
    bounded_handoff,
)
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.pipeline import HandoffEnvelope
from aconsole.core.infrastructure.codegraph.skeleton import python_skeleton, python_slice


class _Scratch:
    def __init__(self) -> None:
        self.prefixes: dict[tuple[str, str, str], str] = {}
        self.seen: set[tuple[str, str, str]] = set()

    def prefix_get(self, plan_id: str, direction_key: str, manifest_sha256: str) -> str | None:
        return self.prefixes.get((plan_id, direction_key, manifest_sha256))

    def prefix_put(self, plan_id: str, direction_key: str, manifest_sha256: str, prefix: str) -> None:
        self.prefixes[plan_id, direction_key, manifest_sha256] = prefix

    def prefix_seen(self, tab_id: str, agent_session_id: str, prefix_sha256: str) -> bool:
        return (tab_id, agent_session_id, prefix_sha256) in self.seen

    def prefix_mark_seen(self, tab_id: str, agent_session_id: str, prefix_sha256: str) -> None:
        self.seen.add((tab_id, agent_session_id, prefix_sha256))


DIRECTION = DirectionContext(
    key="CORE-0",
    invariants="Pure domain",
    scope=("src/**",),
    gate_profile_json='{"tier3": ["pytest"]}',
    skeletons=(("src/a.py", "def public() -> str: ..."),),
)
MODULE = ModuleContext(
    key="CORE-5",
    direction_key="CORE-0",
    objective="Shape context",
    acceptance=("Stable",),
    paths=("src/a.py",),
    test_cmd="pytest",
    contract_sha256="a" * 64,
)
ENVELOPE = HandoffEnvelope(
    node="CORE-5",
    step="patch",
    attempt=1,
    max_attempts=4,
    contract_hash="a" * 64,
    head_block_hash="b" * 64,
    scope=MODULE.paths,
    next="Implement the contract.",
)
SESSION = ContextSession(plan_id="p", tab_id="t", agent_session_id="native-1")
LEASE = LeaseTail(lease_id="lease-1", fence=1, expires_at="2026-11-15T20:00:00Z")


def test_two_attempts_keep_prefix_and_contract_identical() -> None:
    scratch = _Scratch()
    shaper = ContextShaper(scratch)
    first = shaper.shape(session=SESSION, direction=DIRECTION, module=MODULE, envelope=ENVELOPE, lease=LEASE)
    retry = shaper.shape(
        session=replace(SESSION, agent_session_id="native-2"),
        direction=DIRECTION,
        module=MODULE,
        envelope=replace(ENVELOPE, attempt=2, head_block_hash="c" * 64),
        lease=replace(LEASE, fence=2, lease_id="lease-2"),
    )
    assert first.direction_prefix is not None
    assert first.direction_prefix.encode() == retry.direction_prefix.encode()
    assert first.contract.encode() == retry.contract.encode()
    assert first.prefix_sha256 == retry.prefix_sha256
    assert first.handoff != retry.handoff
    assert len(scratch.prefixes) == 1


def test_same_session_gets_digest_and_new_sessions_or_tabs_get_full_prefix() -> None:
    shaper = ContextShaper(_Scratch())
    first = shaper.shape(session=SESSION, direction=DIRECTION, module=MODULE, envelope=ENVELOPE, lease=LEASE)
    second = shaper.shape(session=SESSION, direction=DIRECTION, module=MODULE, envelope=ENVELOPE, lease=LEASE)
    assert second.direction_prefix is None
    assert second.prefix_sha256 == first.prefix_sha256
    assert "direction_prefix" not in second.payload()
    for session in (replace(SESSION, agent_session_id="new"), replace(SESSION, tab_id="other")):
        result = shaper.shape(session=session, direction=DIRECTION, module=MODULE, envelope=ENVELOPE, lease=LEASE)
        assert result.direction_prefix == first.direction_prefix


@pytest.mark.parametrize("field", ["scope", "invariants", "gate_profile_json", "skeletons"])
def test_prefix_manifest_changes_invalidate_delivery_tracking(field: str) -> None:
    changed = {
        "scope": replace(DIRECTION, scope=("src/**", "tests/**")),
        "invariants": replace(DIRECTION, invariants="Changed"),
        "gate_profile_json": replace(DIRECTION, gate_profile_json='{"tier3": ["ruff"]}'),
        "skeletons": replace(DIRECTION, skeletons=(("src/a.py", "def changed() -> int: ..."),)),
    }[field]
    shaper = ContextShaper(_Scratch())
    first = shaper.shape(session=SESSION, direction=DIRECTION, module=MODULE, envelope=ENVELOPE, lease=LEASE)
    second = shaper.shape(session=SESSION, direction=changed, module=MODULE, envelope=ENVELOPE, lease=LEASE)
    assert second.direction_prefix is not None
    assert first.prefix_sha256 != second.prefix_sha256


def test_unordered_direction_inputs_produce_identical_bytes() -> None:
    direction = replace(
        DIRECTION,
        scope=("tests/**", "src/**"),
        gate_profile_json='{"z": 2, "a": 1}',
        skeletons=(*DIRECTION.skeletons, ("src/b.py", "class Other: ...")),
    )
    reordered = replace(
        direction,
        scope=tuple(reversed(direction.scope)),
        gate_profile_json=' { "a" : 1, "z" : 2 } ',
        skeletons=tuple(reversed(direction.skeletons)),
    )
    shaper = ContextShaper(_Scratch())
    first = shaper.shape(session=SESSION, direction=direction, module=MODULE, envelope=ENVELOPE, lease=LEASE)
    second = shaper.shape(
        session=replace(SESSION, agent_session_id="fresh"),
        direction=reordered,
        module=MODULE,
        envelope=ENVELOPE,
        lease=LEASE,
    )
    assert first.direction_prefix == second.direction_prefix


def test_volatile_lease_fields_are_the_final_payload_keys() -> None:
    result = ContextShaper(_Scratch()).shape(
        session=SESSION,
        direction=DIRECTION,
        module=MODULE,
        envelope=ENVELOPE,
        lease=LEASE,
        file_slice="1: def public():",
    )
    assert list(result.payload()) == [
        "direction_prefix",
        "prefix_sha256",
        "contract",
        "handoff",
        "file_slice",
        "lease_id",
        "fence",
        "expires_at",
    ]


def test_unknown_native_session_resends_context_each_time() -> None:
    shaper = ContextShaper(_Scratch())
    for _ in range(2):
        result = shaper.shape(
            session=replace(SESSION, agent_session_id=""),
            direction=DIRECTION,
            module=MODULE,
            envelope=ENVELOPE,
            lease=LEASE,
        )
        assert result.direction_prefix is not None


def test_losing_scratch_cache_resends_the_same_prefix() -> None:
    first = ContextShaper(_Scratch()).shape(
        session=SESSION, direction=DIRECTION, module=MODULE, envelope=ENVELOPE, lease=LEASE
    )
    cold = ContextShaper(_Scratch()).shape(
        session=SESSION, direction=DIRECTION, module=MODULE, envelope=ENVELOPE, lease=LEASE
    )
    assert first.direction_prefix == cold.direction_prefix
    assert first.contract == cold.contract


def test_handoff_drops_remaining_items_before_symbols_and_counts_utf8_bytes() -> None:
    envelope = replace(ENVELOPE, remaining_items=("я" * 160,) * 5, symbols=("public", "Other"))
    body = bounded_handoff(envelope)
    decoded = json.loads(body)
    assert len(body.encode()) <= 1600
    assert len(decoded["remaining_items"]) < 5
    assert decoded["symbols"] == ["public", "Other"]
    assert decoded["scope"] == list(envelope.scope)


def test_handoff_trims_symbols_only_after_all_remaining_items() -> None:
    envelope = replace(ENVELOPE, remaining_items=("fix",), symbols=("Symbol" * 50,) * 12)
    decoded = json.loads(bounded_handoff(envelope))
    assert decoded["remaining_items"] == []
    assert 0 < len(decoded["symbols"]) < 12
    assert decoded["scope"] == list(envelope.scope)


def test_oversized_scope_refuses_without_marking_prefix_as_delivered() -> None:
    scratch = _Scratch()
    shaper = ContextShaper(scratch)
    paths = tuple(f"src/{number}/" + "я" * 100 for number in range(20))
    with pytest.raises(DomainError, match="RESPONSE_TOO_LARGE"):
        shaper.shape(
            session=SESSION,
            direction=DIRECTION,
            module=replace(MODULE, paths=paths),
            envelope=replace(ENVELOPE, scope=paths),
            lease=LEASE,
        )
    assert not scratch.seen
    assert not scratch.prefixes


@pytest.mark.parametrize(
    "envelope",
    [
        replace(ENVELOPE, scope=("outside.py",)),
        replace(ENVELOPE, node="CORE-6"),
        replace(ENVELOPE, contract_hash="wrong"),
    ],
)
def test_handoff_cannot_replace_contract_permissions(envelope: HandoffEnvelope) -> None:
    with pytest.raises(DomainError, match="NODE_CONTRACT"):
        ContextShaper(_Scratch()).shape(
            session=SESSION, direction=DIRECTION, module=MODULE, envelope=envelope, lease=LEASE
        )


def test_python_skeleton_preserves_public_api_and_removes_implementation() -> None:
    source = '''
raise RuntimeError("This source must never run")
class Public[T](Base):
    count: int = expensive()
    def __init__(self, value: T):
        self.secret = value
    @property
    def value(self) -> T:
        return self.secret
    async def run(self, /, *, count: int = 2) -> list[T]:
        def nested(): pass
        return [self.secret] * count
    def _private(self): return "hidden"
def public[T](value: T = None, *args: str, **kwargs: int) -> T:
    """Private implementation docstring."""
    return value
def _private(): pass
type Alias = list[str]
'''
    skeleton = python_skeleton(source)
    ast.parse(skeleton)
    assert "class Public[T](Base)" in skeleton
    assert "async def run" in skeleton
    assert "@property" in skeleton
    assert "count: int" in skeleton
    assert "def __init__" in skeleton
    assert "type Alias = list[str]" in skeleton
    assert all(
        text not in skeleton for text in ("secret", "RuntimeError", "expensive", "nested", "_private", "docstring")
    )


def test_skeleton_of_two_thousand_lines_is_under_fifteen_percent() -> None:
    source = "def public(value: int) -> int:\n" + "    value += 1\n" * 1998 + "    return value\n"
    assert len(source.splitlines()) == 2000
    assert len(python_skeleton(source).encode()) < len(source.encode()) * 0.15


def test_symbol_source_slice_is_numbered_and_bounded() -> None:
    source = "class Public:\n    def run(self):\n" + "        result = 1\n" * 200
    sliced = python_slice(source, ("Public.run",), max_lines=5)
    assert sliced.startswith("1: class Public:")
    assert "2:     def run" in sliced
    assert len(sliced.splitlines()) == 5
    assert python_slice(source, ("missing",)) == ""


def test_broken_source_has_no_skeleton_or_slice() -> None:
    assert python_skeleton("def unfinished(") == ""
    assert python_slice("def unfinished(", ("unfinished",)) == ""
