"""Stable context blocks followed by a bounded handoff, file slice and lease tail."""

import hashlib
import json
from dataclasses import asdict, dataclass, replace

from aconsole.core.application.ports.scratch_store import ScratchStore
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.pipeline import HandoffEnvelope

_ENVELOPE_BYTES = 1600
_SYMBOLS = 12
_REMAINING_ITEMS = 5
_ITEM_CHARS = 160
_NEXT_CHARS = 200
_VERDICT_CHARS = 500


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True, kw_only=True)
class DirectionContext:
    key: str
    invariants: str
    scope: tuple[str, ...]
    gate_profile_json: str
    skeletons: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, kw_only=True)
class ModuleContext:
    key: str
    direction_key: str
    objective: str
    acceptance: tuple[str, ...]
    paths: tuple[str, ...]
    test_cmd: str | None
    contract_sha256: str
    boundaries: str | None = None
    output_format: str | None = None
    blast_radius: tuple[str, ...] | None = None


@dataclass(frozen=True, kw_only=True)
class ContextSession:
    plan_id: str
    tab_id: str
    agent_session_id: str


@dataclass(frozen=True, kw_only=True)
class LeaseTail:
    lease_id: str
    fence: int
    expires_at: str


@dataclass(frozen=True, kw_only=True)
class ShapedContext:
    direction_prefix: str | None
    prefix_sha256: str
    contract: str
    handoff: str
    file_slice: str | None
    lease: LeaseTail

    def payload(self) -> dict[str, object]:
        """Insertion order is intentional; volatile lease fields always come last."""
        result: dict[str, object] = {}
        if self.direction_prefix is not None:
            result["direction_prefix"] = self.direction_prefix
        result.update(prefix_sha256=self.prefix_sha256, contract=self.contract, handoff=self.handoff)
        if self.file_slice is not None:
            result["file_slice"] = self.file_slice
        result.update(lease_id=self.lease.lease_id, fence=self.lease.fence, expires_at=self.lease.expires_at)
        return result


def bounded_handoff(envelope: HandoffEnvelope) -> str:
    """Respect wire limits, then discard remaining items before affected symbols.

    Scope, hashes, attempt and step are never shortened. An oversized mandatory
    payload is refused instead of issuing an incomplete file permission contract.
    """
    current = replace(
        envelope,
        symbols=envelope.symbols[:_SYMBOLS],
        remaining_items=tuple(item[:_ITEM_CHARS] for item in envelope.remaining_items[:_REMAINING_ITEMS]),
        next=envelope.next[:_NEXT_CHARS],
        compact_verdict=None if envelope.compact_verdict is None else envelope.compact_verdict[:_VERDICT_CHARS],
    )
    while True:
        body = _json(asdict(current))
        if len(body.encode()) <= _ENVELOPE_BYTES:
            return body
        if current.remaining_items:
            current = replace(current, remaining_items=current.remaining_items[:-1])
        elif current.symbols:
            current = replace(current, symbols=current.symbols[:-1])
        else:
            raise DomainError(code=-32000, name="RESPONSE_TOO_LARGE", retryable=False)


class ContextShaper:
    def __init__(self, scratch: ScratchStore) -> None:
        self._scratch: ScratchStore = scratch

    def shape(  # noqa: PLR0913 -- the response consists of these independent context blocks
        self,
        *,
        session: ContextSession,
        direction: DirectionContext,
        module: ModuleContext,
        envelope: HandoffEnvelope,
        lease: LeaseTail,
        file_slice: str | None = None,
    ) -> ShapedContext:
        if (
            module.direction_key != direction.key
            or envelope.node != module.key
            or envelope.scope != module.paths
            or envelope.contract_hash != module.contract_sha256
        ):
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
        # Build the potentially failing block before updating delivery tracking.
        handoff = bounded_handoff(envelope)
        prefix = _json(
            {
                "direction": direction.key,
                "invariants": direction.invariants,
                "scope": sorted(direction.scope),
                "gate_profile": json.loads(direction.gate_profile_json),
                "skeletons": sorted(direction.skeletons),
            }
        )
        digest = hashlib.sha256(prefix.encode()).hexdigest()
        cached = self._scratch.prefix_get(session.plan_id, direction.key, digest)
        if cached != prefix:
            self._scratch.prefix_put(session.plan_id, direction.key, digest, prefix)
        contract = _json(asdict(module))
        seen = False
        if session.tab_id and session.agent_session_id:
            seen = self._scratch.prefix_seen(session.tab_id, session.agent_session_id, digest)
            self._scratch.prefix_mark_seen(session.tab_id, session.agent_session_id, digest)
        return ShapedContext(
            direction_prefix=None if seen else prefix,
            prefix_sha256=digest,
            contract=contract,
            handoff=handoff,
            file_slice=file_slice,
            lease=lease,
        )
