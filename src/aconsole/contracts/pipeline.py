"""Wire representations of L2 blocks and bounded handoff content."""

from typing import Annotated, Literal

import msgspec

from aconsole.contracts.common import NodeId

Step = Literal["spec", "patch", "test", "verify"]
Attempt = Annotated[int, msgspec.Meta(ge=1, le=4)]
Sequence = Annotated[int, msgspec.Meta(ge=1, le=4)]


class StateBlock(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    v: Literal[1] = 1
    node_id: NodeId
    attempt: Attempt
    seq: Sequence
    step_type: Step
    prev_block_hash: str
    contract_hash: str
    delta_ref: str | None = None
    delta_sha256: str | None = None
    diff_stat: str | None = None
    affected_symbols: tuple[str, ...] = ()
    test_exit_code: int | None = None
    verification_receipt_hash: str | None = None
    token_cost_usd: Annotated[float, msgspec.Meta(ge=0)] | None = None


class HandoffEnvelope(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    v: Literal[1] = 1
    node: str
    step: Step
    attempt: Attempt
    max_attempts: Attempt
    contract_hash: str
    head_block_hash: str
    scope: tuple[str, ...]
    next: Annotated[str, msgspec.Meta(max_length=200)]
    symbols: Annotated[tuple[str, ...], msgspec.Meta(max_length=12)] = ()
    remaining_items: Annotated[
        tuple[Annotated[str, msgspec.Meta(max_length=160)], ...], msgspec.Meta(max_length=5)
    ] = ()
    compact_verdict: Annotated[str, msgspec.Meta(max_length=500)] | None = None
    diff_stat: str | None = None
