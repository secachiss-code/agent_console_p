"""Lease-authorized L2 execution. Every step advances one durable chain, never the graph."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Literal, Protocol, final

from aconsole.core.application.ports.gate_runner import GateRunnerUnavailableError
from aconsole.core.application.services.context_shaper import (
    ContextSession,
    ContextShaper,
    DirectionContext,
    LeaseTail,
    ModuleContext,
)
from aconsole.core.application.services.gates import GateState, MicroOutcome, MicroProfile, MicroRequest
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.pipeline import (
    HandoffEnvelope,
    PipelineState,
    StateBlock,
    Step,
    StepResult,
    block_hash,
    transition,
)


@dataclass(frozen=True, kw_only=True)
class PipelineActor:
    tab_id: str
    project_id: str
    worktree: Path
    mode: Literal["execute", "plan"] = "execute"
    agent: str = ""
    model: str | None = None
    agent_session_id: str = ""
    container: str = ""


@dataclass(frozen=True, kw_only=True)
class PipelineContract:
    plan_id: str
    node_id: str
    direction: DirectionContext
    module: ModuleContext
    lease: LeaseTail
    profile: MicroProfile
    gates_sha256: str
    max_attempts: int = 4


@dataclass(frozen=True, kw_only=True)
class Attempt:
    id: str
    number: int
    base_oid: str
    status: str
    head_block: str
    compact_verdict: str | None = None


class PipelineBook(Protocol):
    def contract(self, actor: PipelineActor, node_id: str, fence: int, *, review: bool = False) -> PipelineContract: ...
    def scope(self, actor: PipelineActor, node_id: str, fence: int, *, review: bool = False) -> None: ...
    def escalate(self, actor: PipelineActor, node_id: str, fence: int, kind: str, reason: str) -> str: ...
    def exhausted(self, actor: PipelineActor, node_id: str, fence: int) -> None: ...


class PipelineStore(Protocol):
    def history(self, node_id: str) -> tuple[Attempt, ...]: ...
    def blocks(self, attempt_id: str) -> tuple[StateBlock, ...]: ...
    def start(self, contract: PipelineContract, actor: PipelineActor, number: int, base_oid: str) -> Attempt: ...
    def append(
        self, attempt_id: str, block: StateBlock, *, actor: PipelineActor, contract: PipelineContract
    ) -> str: ...
    def finish(
        self,
        attempt_id: str,
        status: Literal["passed", "failed", "abandoned"],
        verdict: str,
        *,
        actor: PipelineActor,
        contract: PipelineContract,
    ) -> None: ...
    def receipt_state(self, receipt_hash: str) -> str | None: ...


class PipelineWorkspace(Protocol):
    def head(self, actor: PipelineActor) -> str: ...
    def capture(self, actor: PipelineActor, contract: PipelineContract, attempt: Attempt, seq: int) -> GateState: ...
    def changed(self, actor: PipelineActor, attempt: Attempt) -> tuple[str, ...]: ...
    def tests(self, actor: PipelineActor, changed: tuple[str, ...]) -> tuple[str, ...]: ...
    def blast_tests(self, actor: PipelineActor, attempt: Attempt) -> tuple[str, ...]: ...
    def reset(self, actor: PipelineActor, contract: PipelineContract, ref: str) -> None: ...
    def context(
        self, actor: PipelineActor, contract: PipelineContract, symbols: tuple[str, ...]
    ) -> tuple[DirectionContext, str | None]: ...


class PipelineScratch(Protocol):
    def record(self, contract: PipelineContract, actor: PipelineActor, state: PipelineState) -> None: ...


class PreparedPatch(Protocol):
    @property
    def passed(self) -> bool: ...

    @property
    def verdict(self) -> str: ...

    @property
    def outcome(self) -> MicroOutcome | None: ...

    def publish(self) -> None: ...


@final
class PipelineService:
    def __init__(  # noqa: PLR0913 -- explicit ports keep execution and storage out of application policy
        self,
        *,
        book: PipelineBook,
        store: PipelineStore,
        workspace: PipelineWorkspace,
        scratch: PipelineScratch,
        shaper: ContextShaper,
        micro: Callable[[PipelineActor, PipelineContract, Attempt, MicroRequest], MicroOutcome],
        blast: Callable[[PipelineActor, PipelineContract, Attempt, MicroRequest, bool], MicroOutcome | None]
        | None = None,
        preflight: Callable[
            [PipelineActor, PipelineContract, Attempt, MicroRequest, str], AbstractContextManager[PreparedPatch]
        ]
        | None = None,
    ) -> None:
        self._book = book
        self._store = store
        self._workspace = workspace
        self._scratch = scratch
        self._shaper = shaper
        self._micro = micro
        self._blast = blast
        self._preflight = preflight
        self._guard = RLock()
        self._locks: dict[str, RLock] = {}

    def _lock(self, node_id: str) -> RLock:
        with self._guard:
            return self._locks.setdefault(node_id, RLock())

    def begin(self, actor: PipelineActor, node_id: str, fence: int) -> dict[str, object]:
        with self._lock(node_id):
            return self._begin(actor, node_id, fence)

    def _begin(self, actor: PipelineActor, node_id: str, fence: int) -> dict[str, object]:
        contract = self._book.contract(actor, node_id, fence)
        history = self._store.history(node_id)
        changed = False
        if history and history[-1].status in {"running", "passed"}:
            blocks = self._store.blocks(history[-1].id)
            if blocks and blocks[-1].step_type == "verify":
                last = blocks[-1]
                self._book.scope(actor, node_id, fence)
                current = self._workspace.capture(actor, contract, history[-1], last.seq)
                changed = self._store.receipt_state(last.verification_receipt_hash or "") != current.state_hash
                if changed and history[-1].status == "running":
                    self._store.finish(
                        history[-1].id, "failed", "State changed after the micro gate", actor=actor, contract=contract
                    )
                    history = self._store.history(node_id)
        if not history or history[-1].status in {"failed", "abandoned"} or changed:
            number = history[-1].number + 1 if history else 1
            if number > contract.max_attempts:
                self._book.exhausted(actor, node_id, fence)
                raise DomainError(code=4016, name="ATTEMPTS_EXHAUSTED", retryable=False)
            attempt = self._store.start(contract, actor, number, self._workspace.head(actor))
        else:
            attempt = history[-1]
        state = self._state(contract, attempt)
        self._scratch.record(contract, actor, state)
        symbols = next(
            (block.affected_symbols for block in reversed(self._store.blocks(attempt.id)) if block.affected_symbols), ()
        )
        direction, sliced = self._workspace.context(actor, contract, symbols)
        envelope = HandoffEnvelope(
            node=contract.module.key,
            step=state.step or "verify",
            attempt=attempt.number,
            max_attempts=contract.max_attempts,
            contract_hash=contract.module.contract_sha256,
            head_block_hash=state.head_block_hash,
            scope=contract.module.paths,
            compact_verdict=attempt.compact_verdict or (history[-1].compact_verdict if history else None),
            symbols=symbols,
            next="Run the blast gate." if state.step is None else f"Complete the {state.step} step.",
        )
        return self._shaper.shape(
            session=ContextSession(
                plan_id=contract.plan_id, tab_id=actor.tab_id, agent_session_id=actor.agent_session_id
            ),
            direction=direction,
            module=contract.module,
            envelope=envelope,
            lease=contract.lease,
            file_slice=sliced,
        ).payload()

    def submit(  # noqa: PLR0913 -- compatible step API plus optional patch
        self,
        actor: PipelineActor,
        node_id: str,
        fence: int,
        step: Step,
        affected_symbols: tuple[str, ...] = (),
        *,
        patch: str | None = None,
    ) -> dict[str, object]:
        with self._lock(node_id):
            return self._submit(actor, node_id, fence, step, affected_symbols, patch=patch)

    def _submit(  # noqa: C901, PLR0912, PLR0913 -- authorize, preflight, then advance one durable step
        self,
        actor: PipelineActor,
        node_id: str,
        fence: int,
        step: Step,
        affected_symbols: tuple[str, ...],
        *,
        patch: str | None = None,
    ) -> dict[str, object]:
        _execute(actor)
        contract, attempt = self._active(actor, node_id, fence)
        self._book.scope(actor, node_id, fence)
        state = self._state(contract, attempt)
        blocks = self._store.blocks(attempt.id)
        if state.step != step and (not blocks or blocks[-1].step_type != step):
            raise DomainError(code=4017, name="STEP_OUT_OF_ORDER", retryable=False)
        if patch:
            if len(patch.encode("utf-8")) > 256 * 1024 or "\x00" in patch:
                raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
            if state.step != step:
                raise DomainError(code=4017, name="STEP_OUT_OF_ORDER", retryable=False)
            if self._preflight is None:
                raise GateRunnerUnavailableError
            request = MicroRequest(
                node_id=node_id,
                contract_hash=contract.module.contract_sha256,
                gates_hash=contract.gates_sha256,
                pipeline=state,
                changed_files=(),
                test_files=(),
            )
            with self._preflight(actor, contract, attempt, request, patch) as prepared:
                if not prepared.passed:
                    outcome = prepared.outcome
                    exhausted = outcome.exhausted if outcome is not None else False
                    if exhausted:
                        self._book.exhausted(actor, node_id, fence)
                    return {
                        "passed": False,
                        "compact_verdict": " ".join(prepared.verdict.split())[:499],
                        "cached": outcome.cached if outcome is not None else False,
                        "exhausted": exhausted,
                        "receipt_hash": outcome.receipt.receipt_hash if outcome is not None else None,
                    }
                _ = self._book.contract(actor, node_id, fence)
                self._book.scope(actor, node_id, fence)
                prepared.publish()
        snapshot = self._workspace.capture(actor, contract, attempt, state.seq)
        if blocks and blocks[-1].step_type == step:
            previous = blocks[-1]
            if previous.delta_sha256 == snapshot.delta_sha256 and previous.affected_symbols == affected_symbols:
                return _submitted(previous, state)
        if state.step != step:
            raise DomainError(code=4017, name="STEP_OUT_OF_ORDER", retryable=False)
        receipt: str | None = None
        if step == "verify":
            changed = self._workspace.changed(actor, attempt)
            outcome = self._micro(
                actor,
                contract,
                attempt,
                MicroRequest(
                    node_id=node_id,
                    contract_hash=contract.module.contract_sha256,
                    gates_hash=contract.gates_sha256,
                    pipeline=state,
                    changed_files=changed,
                    test_files=self._workspace.tests(actor, changed),
                ),
            )
            _ = self._book.contract(actor, node_id, fence)
            self._book.scope(actor, node_id, fence)
            if not outcome.receipt.passed:
                if outcome.exhausted:
                    self._book.exhausted(actor, node_id, fence)
                return {
                    "passed": False,
                    "receipt_hash": outcome.receipt.receipt_hash,
                    "compact_verdict": outcome.receipt.compact_verdict,
                    "exhausted": outcome.exhausted,
                }
            snapshot = outcome.receipt.state
            receipt = outcome.receipt.receipt_hash
        block = StateBlock(
            node_id=node_id,
            attempt=attempt.number,
            seq=state.seq,
            step_type=step,
            prev_block_hash=state.head_block_hash,
            contract_hash=contract.module.contract_sha256,
            delta_ref=snapshot.delta_ref,
            delta_sha256=snapshot.delta_sha256,
            affected_symbols=affected_symbols,
            verification_receipt_hash=receipt,
            test_exit_code=0 if receipt is not None else None,
        )
        _ = self._book.contract(actor, node_id, fence)
        _ = self._store.append(attempt.id, block, actor=actor, contract=contract)
        advanced = transition(state, StepResult(step=step, block_hash=block_hash(block)))
        # The attempt stays running until Tier 2 accepts the completed L2 chain.
        self._scratch.record(contract, actor, advanced)
        return _submitted(block, advanced)

    def escalate(self, actor: PipelineActor, node_id: str, fence: int, kind: str, reason: str) -> dict[str, object]:
        _execute(actor)
        if not kind.strip() or not reason.strip():
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
        with self._lock(node_id):
            request_id = self._book.escalate(actor, node_id, fence, kind.strip(), reason.strip())
            return {"status": "blocked", "request_id": request_id, "reason": "REPLAN_INTERRUPT"}

    def reset(self, actor: PipelineActor, node_id: str, fence: int) -> dict[str, object]:
        with self._lock(node_id):
            return self._reset(actor, node_id, fence)

    def _reset(self, actor: PipelineActor, node_id: str, fence: int) -> dict[str, object]:
        _execute(actor)
        contract, attempt = self._active(actor, node_id, fence)
        green = [
            block
            for record in self._store.history(node_id)
            for block in self._store.blocks(record.id)
            if block.step_type == "verify" and block.verification_receipt_hash and block.delta_ref
        ]
        ref = green[-1].delta_ref if green else attempt.base_oid
        if ref is None:
            raise DomainError(code=4017, name="STEP_OUT_OF_ORDER", retryable=False)
        self._workspace.reset(actor, contract, ref)
        return {"reset_to": ref}

    def gate_run(self, actor: PipelineActor, node_id: str, fence: int, tier: str) -> dict[str, object]:
        _execute(actor)
        if tier == "blast":
            return self.blast_gate(actor, node_id, fence)
        if tier != "micro":
            raise DomainError(code=4018, name="GATE_RECEIPT_REQUIRED", retryable=False)
        with self._lock(node_id):
            contract, attempt = self._active(actor, node_id, fence)
            state = self._state(contract, attempt)
            if state.step is None:
                previous = self._store.blocks(attempt.id)[-1]
                self._book.scope(actor, node_id, fence)
                current = self._workspace.capture(actor, contract, attempt, previous.seq)
                if self._store.receipt_state(previous.verification_receipt_hash or "") != current.state_hash:
                    raise DomainError(code=4018, name="GATE_RECEIPT_REQUIRED", retryable=False)
                return _submitted(previous, state)
            return self._submit(actor, node_id, fence, "verify", ())

    def blast_gate(
        self, actor: PipelineActor, node_id: str, fence: int, *, check_only: bool = False
    ) -> dict[str, object]:
        _execute(actor)
        with self._lock(node_id):
            contract = self._book.contract(actor, node_id, fence, review=True)
            history = self._store.history(node_id)
            if self._blast is None or not history:
                raise DomainError(code=4018, name="GATE_RECEIPT_REQUIRED", retryable=False)
            attempt = history[-1]
            state = self._state(contract, attempt)
            if state.step is not None:
                name, code = ("GATE_RECEIPT_REQUIRED", 4018) if check_only else ("STEP_OUT_OF_ORDER", 4017)
                raise DomainError(code=code, name=name, retryable=False)
            self._book.scope(actor, node_id, fence, review=True)
            blocks = self._store.blocks(attempt.id)
            current = self._workspace.capture(actor, contract, attempt, blocks[-1].seq)
            if self._store.receipt_state(blocks[-1].verification_receipt_hash or "") != current.state_hash:
                raise DomainError(code=4018, name="GATE_RECEIPT_REQUIRED", retryable=False)
            request = MicroRequest(
                node_id=node_id,
                contract_hash=contract.module.contract_sha256,
                gates_hash=contract.gates_sha256,
                pipeline=state,
                changed_files=(),
                test_files=self._workspace.blast_tests(actor, attempt),
            )
            outcome = self._blast(actor, contract, attempt, request, check_only or attempt.status == "passed")
            if outcome is None or (check_only and not outcome.receipt.passed):
                raise DomainError(code=4018, name="GATE_RECEIPT_REQUIRED", retryable=False)
            if outcome.exhausted:
                self._book.exhausted(actor, node_id, fence)
            return {
                "passed": outcome.receipt.passed,
                "receipt_hash": outcome.receipt.receipt_hash,
                "state_hash": outcome.receipt.state.state_hash,
                "cached": outcome.cached,
                "exhausted": outcome.exhausted,
                "verdict": outcome.receipt.compact_verdict,
            }

    def _active(self, actor: PipelineActor, node_id: str, fence: int) -> tuple[PipelineContract, Attempt]:
        contract = self._book.contract(actor, node_id, fence)
        history = self._store.history(node_id)
        if not history or history[-1].status not in {"running", "passed"}:
            raise DomainError(code=4017, name="STEP_OUT_OF_ORDER", retryable=False)
        return contract, history[-1]

    def _state(self, contract: PipelineContract, attempt: Attempt) -> PipelineState:
        blocks = self._store.blocks(attempt.id)
        state = PipelineState(
            attempt=attempt.number,
            max_attempts=contract.max_attempts,
            step="spec" if attempt.number == 1 else "patch",
            seq=1,
            head_block_hash=blocks[0].prev_block_hash if blocks else attempt.head_block,
        )
        for block in blocks:
            state = transition(state, StepResult(step=block.step_type, block_hash=block_hash(block)))
        return state


def _execute(actor: PipelineActor) -> None:
    if actor.mode != "execute":
        raise DomainError(code=4012, name="PLAN_ROLE", retryable=False)


def _submitted(block: StateBlock, state: PipelineState) -> dict[str, object]:
    return {
        "block_hash": block_hash(block),
        "step": block.step_type,
        "attempt": block.attempt,
        "next_step": state.step,
        "receipt_hash": block.verification_receipt_hash,
        "passed": True,
    }
