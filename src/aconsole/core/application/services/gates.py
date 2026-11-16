"""Kernel-owned verification, with normalization before recording the checked state."""

import hashlib
import json
import shlex
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Protocol, final

from aconsole.core.application.ports.gate_runner import GateResult, GateRunner
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.pipeline import PipelineState, retry

_MICRO = "micro"
_MAX_TEST_FILES = 3
_MAX_VERDICT = 500
_MAX_ITEMS = 5
_MAX_ITEM_CHARS = 160


@dataclass(frozen=True, kw_only=True)
class GateState:
    """Snapshot supplied by the kernel's diff reader, never by an MCP client."""

    state_hash: str
    delta_ref: str
    delta_sha256: str


@dataclass(frozen=True, kw_only=True)
class MicroProfile:
    """Trusted argv prefixes read from the frozen direction contract."""

    format: tuple[str, ...]
    fix: tuple[str, ...]
    lint: tuple[str, ...]
    types: tuple[str, ...]
    tests: tuple[str, ...]

    def commands(self, changed_files: tuple[str, ...], test_files: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
        files = _paths(changed_files)
        tests = _paths(test_files)[:_MAX_TEST_FILES]
        if any(
            not argv or any("\x00" in arg for arg in argv)
            for argv in (self.format, self.fix, self.lint, self.types, self.tests)
        ):
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
        commands = tuple((*argv, *files) for argv in (self.format, self.fix, self.lint, self.types)) if files else ()
        return (*commands, (*self.tests, *tests)) if tests else commands


@dataclass(frozen=True, kw_only=True)
class MicroRequest:
    """Kernel-owned scope and test candidates selected from one code-graph hop."""

    node_id: str
    contract_hash: str
    gates_hash: str
    pipeline: PipelineState
    changed_files: tuple[str, ...]
    test_files: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class MicroReceipt:
    node_id: str
    state: GateState
    argv_sha256: str
    passed: bool
    compact_verdict: str
    remaining_items: tuple[str, ...]
    results: tuple[GateResult, ...]

    @property
    def receipt_hash(self) -> str:
        return _digest((self.node_id, self.state.state_hash, self.argv_sha256, self.passed, self.compact_verdict))


@dataclass(frozen=True, kw_only=True)
class MicroOutcome:
    receipt: MicroReceipt
    pipeline: PipelineState
    exhausted: bool = False
    cached: bool = False


class GateCache(Protocol):
    """Disposable cache; durable receipts remain the audit store's responsibility."""

    def get(self, state_hash: str, tier: str) -> MicroOutcome | None: ...

    def put(self, state_hash: str, tier: str, outcome: MicroOutcome) -> None: ...


class GateAudit(Protocol):
    def record(self, request: MicroRequest, outcome: MicroOutcome) -> None:
        """Atomically persist the receipt, failure verdict and consumed attempt."""
        ...


@final
class MicroGate:
    def __init__(
        self,
        *,
        bind_runner: Callable[[tuple[tuple[str, ...], ...]], GateRunner],
        capture: Callable[[], GateState],
        cache: GateCache,
        audit: GateAudit,
    ) -> None:
        self._bind_runner = bind_runner
        self._capture = capture
        self._cache = cache
        self._audit = audit

    def run(self, request: MicroRequest, profile: MicroProfile) -> MicroOutcome:
        if request.pipeline.step != "verify":
            raise DomainError(code=4017, name="STEP_OUT_OF_ORDER", retryable=False)
        commands = profile.commands(request.changed_files, request.test_files)
        argv_hash = _digest(commands)
        initial = self._capture()
        initial_key = _cache_key(request, initial, argv_hash)
        cached = self._cache.get(initial_key, _MICRO)
        if cached is not None:
            return replace(cached, cached=True)
        runner = self._bind_runner(commands)
        results: list[GateResult] = []
        for argv in commands:
            result = runner.run(argv, tier=1)
            results.append(result)
            if result.returncode != 0:
                break
        # Formatting/fixes (including partial changes on failure) belong to delta.
        checked = self._capture()
        passed = all(result.returncode == 0 for result in results)
        items = tuple(_failure(result)[:_MAX_ITEM_CHARS] for result in results if result.returncode != 0)[:_MAX_ITEMS]
        verdict = f"{sum(result.returncode == 0 for result in results)} passed, {len(items)} failed"
        if items:
            verdict += "\n" + "\n".join(items)
        receipt = MicroReceipt(
            node_id=request.node_id,
            state=checked,
            argv_sha256=argv_hash,
            passed=passed,
            compact_verdict=verdict[:_MAX_VERDICT],
            remaining_items=items,
            results=tuple(results),
        )
        pipeline, exhausted = request.pipeline, False
        if not passed:
            try:
                pipeline = retry(pipeline)
            except DomainError as exc:
                if exc.name != "ATTEMPTS_EXHAUSTED":
                    raise
                exhausted = True
        outcome = MicroOutcome(receipt=receipt, pipeline=pipeline, exhausted=exhausted)
        self._audit.record(request, outcome)
        # Include contract/profile/attempt identity so a retry cannot reuse a stale failure.
        self._cache.put(_cache_key(request, checked, argv_hash), _MICRO, outcome)
        return outcome


@dataclass(frozen=True, kw_only=True)
class BlastProfile:
    test_cmd: str
    tests: tuple[str, ...]

    def argv_hash(self, test_files: tuple[str, ...]) -> str:
        return _digest(("blast", self.commands(test_files)))

    def commands(self, test_files: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
        try:
            primary = tuple(shlex.split(self.test_cmd))
        except ValueError as exc:
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False) from exc
        if not primary or not self.tests or any("\x00" in arg for arg in (*primary, *self.tests)):
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
        # Preserve the CTE's depth priority while validating paths independently.
        _ = _paths(test_files)
        tests = tuple(dict.fromkeys(test_files))[:30]
        return (primary, (*self.tests, *tests)) if tests else (primary,)


@final
class BlastGate:
    def __init__(
        self,
        *,
        bind_runner: Callable[[tuple[tuple[str, ...], ...]], GateRunner],
        capture: Callable[[], GateState],
        cache: GateCache,
        audit: GateAudit,
    ) -> None:
        self._bind_runner, self._capture, self._cache, self._audit = bind_runner, capture, cache, audit

    def run(self, request: MicroRequest, profile: BlastProfile, *, check_only: bool = False) -> MicroOutcome | None:
        if request.pipeline.step is not None:
            raise DomainError(code=4017, name="STEP_OUT_OF_ORDER", retryable=False)
        commands = profile.commands(request.test_files)
        argv_hash = profile.argv_hash(request.test_files)
        initial = self._capture()
        key = _cache_key(request, initial, argv_hash)
        cached = self._cache.get(key, "blast")
        if cached is not None:
            return replace(cached, cached=True)
        if check_only:
            return None
        runner = self._bind_runner(commands)
        results: list[GateResult] = []
        for argv in commands:
            result = runner.run(argv, tier=2)
            results.append(result)
            if result.returncode != 0 or result.timed_out:
                break
        checked = self._capture()
        unchanged = initial.state_hash == checked.state_hash
        passed = unchanged and all(result.returncode == 0 and not result.timed_out for result in results)
        items = tuple(
            _failure(result)[:_MAX_ITEM_CHARS] for result in results if result.returncode != 0 or result.timed_out
        )
        if not unchanged:
            items = (*items, "Workspace changed during the blast gate")
        verdict = f"{sum(result.returncode == 0 for result in results)} passed, {len(items)} failed"
        if items:
            verdict += "\n" + "\n".join(items)
        receipt = MicroReceipt(
            node_id=request.node_id,
            state=initial,
            argv_sha256=argv_hash,
            passed=passed,
            compact_verdict=verdict[:_MAX_VERDICT],
            remaining_items=items[:_MAX_ITEMS],
            results=tuple(results),
        )
        state, exhausted = _blast_retry(request.pipeline, passed=passed)
        outcome = MicroOutcome(receipt=receipt, pipeline=state, exhausted=exhausted)
        self._audit.record(request, outcome)
        # A moving workspace cannot cache a green result under its new content.
        self._cache.put(key, "blast", outcome)
        return outcome


def _blast_retry(state: PipelineState, *, passed: bool) -> tuple[PipelineState, bool]:
    if passed:
        return state, False
    try:
        return retry(state), False
    except DomainError as exc:
        if exc.name != "ATTEMPTS_EXHAUSTED":
            raise
        return state, True


def _paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    for path in paths:
        parsed = PurePosixPath(path)
        if not path or path.startswith("-") or parsed.is_absolute() or ".." in parsed.parts or "\x00" in path:
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
    return tuple(sorted(set(paths)))


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _cache_key(request: MicroRequest, state: GateState, argv_hash: str) -> str:
    return _digest(
        (
            request.node_id,
            request.contract_hash,
            request.gates_hash,
            state.state_hash,
            argv_hash,
            request.pipeline.attempt,
            request.pipeline.head_block_hash,
        )
    )


def _failure(result: GateResult) -> str:
    if result.timed_out:
        return f"{result.argv[0]}: timeout"
    lines = tuple(line.strip() for line in result.output.splitlines() if line.strip())
    error = next((line for line in lines if "error" in line.casefold()), lines[0] if lines else "no output")
    return f"{result.argv[0]}: {error}"
