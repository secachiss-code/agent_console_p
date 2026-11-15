"""L2 ordering, retries and stable block identity without infrastructure imports."""

from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.pipeline import PipelineState, StateBlock, StepResult, block_hash, retry, transition


def test_pipeline_steps_and_retry_limit() -> None:
    state = PipelineState()
    for step in ("spec", "patch", "test"):
        state = transition(state, StepResult(step=step, block_hash=step))
    assert state.step == "verify"
    for attempt in range(2, 5):
        state = transition(state, StepResult(step="verify", block_hash="failed", passed=False))
        assert state.attempt == attempt
        assert state.step == "patch"
        assert state.seq == 1
        assert state.head_block_hash == "failed"
        for step in ("patch", "test"):
            state = transition(state, StepResult(step=step, block_hash=step))
    with pytest.raises(DomainError, match="ATTEMPTS_EXHAUSTED"):
        transition(state, StepResult(step="verify", block_hash="failed", passed=False))
    assert state.attempt == 4


def test_success_and_tier_two_failure() -> None:
    state = PipelineState()
    for step in ("spec", "patch", "test", "verify"):
        state = transition(state, StepResult(step=step, block_hash=step))
    assert state.step is None
    assert retry(state).attempt == 2
    with pytest.raises(DomainError, match="STEP_OUT_OF_ORDER"):
        transition(state, StepResult(step="verify", block_hash="again"))


@pytest.mark.parametrize("step", ["patch", "test", "verify"])
def test_steps_cannot_be_skipped(step: str) -> None:
    with pytest.raises(DomainError, match="STEP_OUT_OF_ORDER"):
        transition(PipelineState(), StepResult(step=step, block_hash="x"))


@given(st.text(), st.text())
def test_hash_is_stable_and_sensitive(left: str, right: str) -> None:
    first = StateBlock(node_id=left, attempt=1, seq=1, step_type="spec", prev_block_hash="p", contract_hash="c")
    assert block_hash(first) == block_hash(replace(first))
    if left != right:
        assert block_hash(first) != block_hash(replace(first, node_id=right))


@pytest.mark.parametrize(("field", "value"), [
    ("v", 2), ("node_id", "other"), ("attempt", 2), ("seq", 2), ("step_type", "patch"),
    ("prev_block_hash", "other"), ("contract_hash", "other"), ("delta_ref", "ref"),
    ("delta_sha256", "digest"), ("diff_stat", "+1"), ("affected_symbols", ("symbol",)),
    ("test_exit_code", 1), ("verification_receipt_hash", "receipt"), ("token_cost_usd", 0.5),
])
def test_every_block_field_contributes_to_hash(field: str, value: object) -> None:
    block = StateBlock(node_id="n", attempt=1, seq=1, step_type="spec", prev_block_hash="p", contract_hash="c")
    assert block_hash(block) != block_hash(replace(block, **{field: value}))


@pytest.mark.parametrize("max_attempts", [1, 2, 3, 4])
def test_retry_respects_configured_limit(max_attempts: int) -> None:
    exhausted = PipelineState(attempt=max_attempts, max_attempts=max_attempts, step="verify")
    with pytest.raises(DomainError, match="ATTEMPTS_EXHAUSTED"):
        retry(exhausted)
    assert exhausted.attempt == max_attempts


def test_genesis_is_bound_to_contract() -> None:
    from aconsole.core.domain.pipeline import genesis_hash

    assert genesis_hash("contract") == genesis_hash("contract")
    assert genesis_hash("contract") != genesis_hash("changed")
    assert len(genesis_hash("contract")) == 64
