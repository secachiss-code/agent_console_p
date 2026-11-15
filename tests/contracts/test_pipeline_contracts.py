"""Wire/domain equality, canonical JSON and strict versioned decoding."""

from dataclasses import asdict

import msgspec
import pytest
from hypothesis import given
from hypothesis import strategies as st

from aconsole.contracts.errors import error_by_name
from aconsole.contracts.pipeline import StateBlock as WireBlock
from aconsole.core.domain.pipeline import StateBlock, canonical_block


@given(cost=st.one_of(st.none(), st.floats(min_value=0, allow_nan=False, allow_infinity=False)),
       node=st.text(alphabet=st.characters(exclude_categories=("Cs",))))
def test_wire_encoding_matches_domain(cost: float | None, node: str) -> None:
    block = StateBlock(node_id=node, attempt=1, seq=1, step_type="spec", prev_block_hash="p",
                       contract_hash="c", token_cost_usd=cost)
    wire = msgspec.convert(asdict(block), WireBlock)
    encoded = msgspec.json.encode(wire, order="sorted")
    assert canonical_block(block) == encoded
    assert msgspec.json.decode(encoded, type=WireBlock) == wire


@pytest.mark.parametrize("patch", [{"v": 2}, {"attempt": 5}, {"seq": 0}, {"step_type": "other"}, {"extra": 1}])
def test_invalid_blocks_rejected(patch: dict[str, object]) -> None:
    body = {"node_id": "n", "attempt": 1, "seq": 1, "step_type": "spec", "prev_block_hash": "p", "contract_hash": "c"}
    body.update(patch)
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(msgspec.json.encode(body), type=WireBlock)


@pytest.mark.parametrize(("code", "name"), [(4015, "SCOPE_VIOLATION"), (4016, "ATTEMPTS_EXHAUSTED"),
                                           (4017, "STEP_OUT_OF_ORDER"), (4018, "GATE_RECEIPT_REQUIRED")])
def test_pipeline_errors_registered(code: int, name: str) -> None:
    assert error_by_name(name).code == code


@pytest.mark.parametrize("patch", [
    {"symbols": ["s"] * 13}, {"remaining_items": ["item"] * 6},
    {"remaining_items": ["x" * 161]}, {"compact_verdict": "x" * 501},
    {"next": "x" * 201}, {"step": "other"}, {"extra": True},
])
def test_handoff_limits_enforced(patch: dict[str, object]) -> None:
    from aconsole.contracts.pipeline import HandoffEnvelope as WireEnvelope
    from aconsole.core.domain.pipeline import HandoffEnvelope

    envelope = HandoffEnvelope(node="CORE-4", step="spec", attempt=1, max_attempts=4,
                               contract_hash="c", head_block_hash="h", scope=("src/**",), next="Confirm symbols")
    body = asdict(envelope)
    wire = msgspec.convert(body, WireEnvelope)
    assert msgspec.json.decode(msgspec.json.encode(wire), type=WireEnvelope) == wire
    body.update(patch)
    with pytest.raises(msgspec.ValidationError):
        msgspec.convert(body, WireEnvelope)
