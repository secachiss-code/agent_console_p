"""Pure L2 transitions and deterministic, infrastructure-free block identity."""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from typing import Literal

from aconsole.core.domain.errors import DomainError

Step = Literal["spec", "patch", "test", "verify"]
GENESIS_TAG = b"aconsole/genesis/v1\0"
_STEPS: tuple[Step, ...] = ("spec", "patch", "test", "verify")
_MAX_ATTEMPTS = 4
_DECIMAL_EXPONENT_MIN = -6


@dataclass(frozen=True, kw_only=True)
class StateBlock:
    v: Literal[1] = 1
    node_id: str
    attempt: int
    seq: int
    step_type: Step
    prev_block_hash: str
    contract_hash: str
    delta_ref: str | None = None
    delta_sha256: str | None = None
    diff_stat: str | None = None
    affected_symbols: tuple[str, ...] = ()
    test_exit_code: int | None = None
    verification_receipt_hash: str | None = None
    token_cost_usd: float | None = None


@dataclass(frozen=True, kw_only=True)
class HandoffEnvelope:
    v: Literal[1] = 1
    node: str
    step: Step
    attempt: int
    max_attempts: int
    contract_hash: str
    head_block_hash: str
    scope: tuple[str, ...]
    next: str
    symbols: tuple[str, ...] = ()
    remaining_items: tuple[str, ...] = ()
    compact_verdict: str | None = None
    diff_stat: str | None = None


def genesis_hash(contract_hash: str) -> str:
    return hashlib.sha256(GENESIS_TAG + contract_hash.encode()).hexdigest()


def _canonical_float(value: float) -> str:
    if not math.isfinite(value) or value < 0:
        message = "token cost must be finite and nonnegative"
        raise ValueError(message)
    encoded = repr(value)
    if "e" in encoded:
        mantissa, exponent = encoded.split("e")
        power = int(exponent)
        return format(Decimal(encoded), "f") if _DECIMAL_EXPONENT_MIN <= power < 0 else f"{mantissa}e{power}"
    return encoded


def canonical_block(block: StateBlock) -> bytes:
    """Sorted compact JSON; float spelling matches the msgspec wire encoder."""
    body: dict[str, object] = asdict(block)
    parts: list[str] = []
    for key in sorted(body):
        value = body[key]
        encoded = (_canonical_float(block.token_cost_usd)
                   if key == "token_cost_usd" and block.token_cost_usd is not None
                   else json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
        parts.append(json.dumps(key) + ":" + encoded)
    return ("{" + ",".join(parts) + "}").encode()


def block_hash(block: StateBlock) -> str:
    return hashlib.sha256(canonical_block(block)).hexdigest()


@dataclass(frozen=True, kw_only=True)
class PipelineState:
    attempt: int = 1
    max_attempts: int = 4
    step: Step | None = "spec"
    seq: int = 1
    head_block_hash: str = ""

    def __post_init__(self) -> None:
        if not 1 <= self.attempt <= self.max_attempts <= _MAX_ATTEMPTS:
            message = "invalid pipeline attempt bounds"
            raise ValueError(message)
        if not 1 <= self.seq <= len(_STEPS):
            message = "invalid pipeline sequence"
            raise ValueError(message)


@dataclass(frozen=True, kw_only=True)
class StepResult:
    step: Step
    block_hash: str
    passed: bool = True


def retry(state: PipelineState) -> PipelineState:
    """A failed Tier 1 or Tier 2 repeats patch/test/verify, retaining the chain head."""
    if state.step not in ("verify", None):
        raise DomainError(code=4017, name="STEP_OUT_OF_ORDER", retryable=False)
    if state.attempt >= state.max_attempts:
        raise DomainError(code=4016, name="ATTEMPTS_EXHAUSTED", retryable=False)
    return replace(state, attempt=state.attempt + 1, step="patch", seq=1)


def transition(state: PipelineState, event: StepResult) -> PipelineState:
    if state.step is None or event.step != state.step:
        raise DomainError(code=4017, name="STEP_OUT_OF_ORDER", retryable=False)
    if not event.passed and event.step != "verify":
        raise DomainError(code=4017, name="STEP_OUT_OF_ORDER", retryable=False)
    updated = replace(state, head_block_hash=event.block_hash)
    if event.step == "verify":
        return replace(updated, step=None) if event.passed else retry(updated)
    return replace(updated, step=_STEPS[_STEPS.index(event.step) + 1], seq=state.seq + 1)
