"""Approval broker boundary. The holding broker arrives with the adapters."""

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from aconsole.contracts.app import AppliedResult, ApprovalView, DecideParams
from aconsole.contracts.common import ApprovalId, ApprovalScope
from aconsole.contracts.events import ApprovalRequested, ApprovalResolved
from aconsole.core.domain.approvals import Approval, ApprovalBook, ApprovalDecision, ApprovalPhase, OpenRequest
from aconsole.core.domain.errors import DomainError


class ApprovalBroker(Protocol):
    """One decision for one approval. Adapters perform the hook reply."""

    def decide(self, approval_id: str, *, behavior: str) -> str: ...


class RecordingBroker:
    """Records decisions. Used until the adapter exists."""

    decisions: list[tuple[str, str]]

    def __init__(self) -> None:
        self.decisions = []

    def decide(self, approval_id: str, *, behavior: str) -> str:
        self.decisions.append((approval_id, behavior))
        return behavior


@dataclass(frozen=True, slots=True)
class HookOutcome:
    phase: ApprovalPhase
    message: str | None = None


class ApprovalHold:
    _book: ApprovalBook
    _publish: Callable[[ApprovalRequested | ApprovalResolved], None]
    _notify: Callable[[Approval], None]
    _audit: Callable[[Approval], None]
    _now: Callable[[], datetime]
    def __init__(
        self,
        book: ApprovalBook,
        *,
        publish: Callable[[ApprovalRequested | ApprovalResolved], None],
        notify: Callable[[Approval], None],
        audit: Callable[[Approval], None],
        now: Callable[[], datetime],
    ) -> None:
        self._book = book
        self._publish = publish
        self._notify = notify
        self._audit = audit
        self._now = now
        self._pending: dict[str, asyncio.Future[HookOutcome]] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._decisions: dict[str, tuple[str, ApprovalDecision]] = {}
        self._settled: set[str] = set()
        self._views: dict[str, ApprovalView] = {}
        self._views_lock: threading.Lock = threading.Lock()

    def pending_views(self) -> tuple[ApprovalView, ...]:
        with self._views_lock:
            return tuple(self._views.values())

    async def hold(self, request: OpenRequest, view: ApprovalView) -> HookOutcome:
        approval = self._book.open(request)
        if approval.phase is not ApprovalPhase.PENDING:
            self._settle(approval, None)
            return HookOutcome(approval.phase)
        future = self._pending.get(approval.id)
        if future is None:
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._pending[approval.id] = future
            with self._views_lock:
                self._views[approval.id] = view
            delay = max(0.0, (approval.expires_at - self._now()).total_seconds())
            self._timers[approval.id] = loop.call_later(delay, self._expire_due, approval.expires_at)
            self._publish(ApprovalRequested(approval=view))
            self._notify(approval)
        return await asyncio.shield(future)

    def decide(self, params: DecideParams, *, at: datetime) -> AppliedResult:
        approval_id = str(params.approval_id)
        decision = ApprovalDecision(params.decision.value)
        previous = self._decisions.get(approval_id)
        if previous is not None:
            if previous[0] != params.nonce:
                raise DomainError(code=3003, name="APPROVAL_NONCE_MISMATCH", retryable=False)
            if previous[1] is decision:
                return AppliedResult(applied=False)
        approval = self._book.decide(
            approval_id,
            nonce=params.nonce,
            decision=decision,
            remember_prefix=params.scope is ApprovalScope.TAB_SESSION,
            at=at,
        )
        self._decisions[approval_id] = (params.nonce, decision)
        self._settle(approval, params.message if decision is ApprovalDecision.DENY else None)
        return AppliedResult(applied=True)

    def expire(self, *, at: datetime) -> None:
        for approval in self._book.expire(at):
            self._settle(approval, None)

    def _expire_due(self, at: datetime) -> None:
        self.expire(at=max(at, self._now()))

    def _settle(self, approval: Approval, message: str | None) -> None:
        if approval.id in self._settled:
            return
        self._settled.add(approval.id)
        with self._views_lock:
            _ = self._views.pop(approval.id, None)
        timer = self._timers.pop(approval.id, None)
        if timer is not None:
            timer.cancel()
        future = self._pending.pop(approval.id, None)
        if future is not None and not future.done():
            future.set_result(HookOutcome(approval.phase, message))
        if approval.by is not None and approval.phase is not ApprovalPhase.PENDING:
            self._publish(ApprovalResolved(
                approval_id=ApprovalId(approval.id), outcome=approval.phase.value, by=approval.by.value,
            ))
        self._audit(approval)
