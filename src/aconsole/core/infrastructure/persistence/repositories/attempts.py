"""Durable pipeline history. All reads and writes are serialized by Writer.

Returned rows are detached snapshots; callers cannot mutate the writer's session.
PipelineService authorizes requests; the optional guard rechecks leases at the Writer boundary.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, final

from sqlalchemy import select
from sqlalchemy.orm import Session

from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.pipeline import StateBlock, block_hash, canonical_block, genesis_hash
from aconsole.core.infrastructure.persistence.cleanup import remove_gate_logs, trim_gate_receipts
from aconsole.core.infrastructure.persistence.models import GateReceipt, Lease, NodeAttempt, NodeStepBlock, PlanNode
from aconsole.core.infrastructure.persistence.writer import Writer


@dataclass(frozen=True, kw_only=True)
class AttemptLease:
    tab_id: str
    fence: int
    at: datetime


def check_attempt_lease(session: Session, node_id: str, guard: AttemptLease | None) -> None:
    if guard is None:
        return
    lease = session.get(Lease, node_id)
    if lease is None:
        raise DomainError(code=4008, name="LEASE_EXPIRED", retryable=False)
    if lease.tab_id != guard.tab_id or lease.fence != guard.fence:
        raise DomainError(code=4007, name="LEASE_STALE_FENCE", retryable=False)
    expires = lease.expires_at.replace(tzinfo=UTC) if lease.expires_at.tzinfo is None else lease.expires_at
    if expires <= guard.at:
        raise DomainError(code=4008, name="LEASE_EXPIRED", retryable=False)


@final
class AttemptDatabase:
    def __init__(self, writer: Writer) -> None:
        self._writer = writer

    def start(self, attempt: NodeAttempt, *, lease: AttemptLease | None = None) -> None:
        """Insert the next attempt without resetting the durable counter."""

        def write(session: Session) -> None:
            check_attempt_lease(session, attempt.node_id, lease)
            node = session.get(PlanNode, attempt.node_id)
            previous = session.scalar(
                select(NodeAttempt)
                .where(
                    NodeAttempt.node_id == attempt.node_id,
                )
                .order_by(NodeAttempt.attempt_no.desc())
                .limit(1)
            )
            number = previous.attempt_no + 1 if previous is not None else 1
            if node is None or attempt.attempt_no != number or number > node.max_attempts:
                message = "invalid next attempt number or exhausted attempt limit"
                raise ValueError(message)
            if previous is not None and previous.status == "running":
                message = "previous attempt is still running"
                raise ValueError(message)
            if attempt.status != "running" or attempt.finished_at is not None:
                message = "new attempt must be running"
                raise ValueError(message)
            # A retry continues the previous block chain even when cache.db vanished.
            expected = previous.head_block if previous is not None else genesis_hash(node.contract_sha256 or "")
            if attempt.head_block not in (None, expected):
                message = "attempt head does not match durable history"
                raise ValueError(message)
            attempt.head_block = expected
            session.add(attempt)
            session.flush()
            session.expunge(attempt)

        _ = self._writer.call(write)

    def attempts(self, node_id: str) -> tuple[NodeAttempt, ...]:
        found: list[NodeAttempt] = []

        def read(session: Session) -> None:
            for row in session.scalars(
                select(NodeAttempt)
                .where(
                    NodeAttempt.node_id == node_id,
                )
                .order_by(NodeAttempt.attempt_no)
            ):
                session.expunge(row)
                found.append(row)

        _ = self._writer.call(read)
        return tuple(found)

    def append(self, attempt_id: str, block: StateBlock, *, now: datetime, lease: AttemptLease | None = None) -> str:
        """Persist a canonical block and its chain head in the same transaction."""
        digest = block_hash(block)
        body = canonical_block(block).decode()

        def write(session: Session) -> None:
            check_attempt_lease(session, block.node_id, lease)
            held = session.get(NodeStepBlock, digest)
            if held is not None:
                if held.attempt_id != attempt_id or held.body != body:
                    message = "block hash belongs to different history"
                    raise ValueError(message)
                return
            attempt = session.get(NodeAttempt, attempt_id)
            if attempt is None or attempt.status != "running":
                message = "no running attempt"
                raise ValueError(message)
            node = session.get(PlanNode, attempt.node_id)
            count = len(session.scalars(select(NodeStepBlock.seq).where(NodeStepBlock.attempt_id == attempt_id)).all())
            if (
                block.node_id != attempt.node_id
                or block.attempt != attempt.attempt_no
                or block.seq != count + 1
                or block.prev_block_hash != attempt.head_block
                or node is None
                or block.contract_hash != (node.contract_sha256 or "")
            ):
                message = "block breaks durable chain or frozen contract"
                raise ValueError(message)
            session.add(
                NodeStepBlock(
                    block_hash=digest,
                    attempt_id=attempt_id,
                    seq=block.seq,
                    step_type=block.step_type,
                    prev_block_hash=block.prev_block_hash,
                    body=body,
                    created_at=now,
                )
            )
            attempt.head_block = digest
            attempt.diff_stat = block.diff_stat
            if block.token_cost_usd is not None:
                attempt.cost_usd = (attempt.cost_usd or 0) + block.token_cost_usd

        _ = self._writer.call(write)
        return digest

    def blocks(self, attempt_id: str) -> tuple[NodeStepBlock, ...]:
        found: list[NodeStepBlock] = []

        def read(session: Session) -> None:
            for row in session.scalars(
                select(NodeStepBlock)
                .where(
                    NodeStepBlock.attempt_id == attempt_id,
                )
                .order_by(NodeStepBlock.seq)
            ):
                session.expunge(row)
                found.append(row)

        _ = self._writer.call(read)
        return tuple(found)

    def finish(
        self,
        attempt_id: str,
        status: Literal["passed", "failed", "abandoned"],
        *,
        now: datetime,
        compact_verdict: str,
        lease: AttemptLease | None = None,
    ) -> None:
        def write(session: Session) -> None:
            row = session.get(NodeAttempt, attempt_id)
            if row is None:
                message = "unknown attempt"
                raise ValueError(message)
            check_attempt_lease(session, row.node_id, lease)
            if row.status != "running":
                if row.status == status and row.compact_verdict == compact_verdict:
                    return
                message = "attempt is already finished"
                raise ValueError(message)
            row.status = status
            row.finished_at = now
            row.compact_verdict = compact_verdict

        _ = self._writer.call(write)

    def save_receipt(self, receipt: GateReceipt) -> None:
        def write(session: Session) -> None:
            held = session.get(GateReceipt, receipt.receipt_hash)
            if held is not None:
                columns = GateReceipt.__table__.columns.keys()
                if any(getattr(held, name) != getattr(receipt, name) for name in columns):
                    message = "receipt hash belongs to different content"
                    raise ValueError(message)
                return
            session.add(receipt)
            session.flush()
            session.expunge(receipt)

        _ = self._writer.call(write)

    def receipt(self, receipt_hash: str) -> GateReceipt | None:
        found: list[GateReceipt] = []

        def read(session: Session) -> None:
            row = session.get(GateReceipt, receipt_hash)
            if row is not None:
                session.expunge(row)
                found.append(row)

        _ = self._writer.call(read)
        return found[0] if found else None

    def prune(self, *, now: datetime, log_root: Path) -> int:
        """Delete expired receipts through Writer, then unlink their committed logs."""
        found: list[tuple[int, tuple[str, ...]]] = []
        _ = self._writer.call(lambda session: found.append(trim_gate_receipts(session, now)))
        count, refs = found[0]
        remove_gate_logs(refs, log_root)
        return count
