"""Tier 1 normalization, cache identity, audit and retry budgets."""

import hashlib
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from aconsole.core.application.ports.gate_runner import GateResult, GateRunner, GateRunnerUnavailableError
from aconsole.core.application.services.gates import (
    GateState,
    MicroGate,
    MicroOutcome,
    MicroProfile,
    MicroRequest,
)
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.pipeline import PipelineState
from aconsole.core.infrastructure.containers.podman import Podman
from aconsole.core.infrastructure.gates.runner import ContainerGateRunner


class _Harness:
    def __init__(self) -> None:
        self.state: GateState = GateState(state_hash="before", delta_ref="ref/1", delta_sha256="diff/1")
        self.cache: dict[tuple[str, str], MicroOutcome] = {}
        self.audit: list[MicroOutcome] = []
        self.calls: list[tuple[str, ...]] = []
        self.bound: tuple[tuple[str, ...], ...] = ()
        self.fail: str | None = None
        self.unavailable: bool = False
        self.normalize: bool = False

    def capture(self) -> GateState:
        return self.state

    def bind(self, commands: tuple[tuple[str, ...], ...]) -> GateRunner:
        self.bound = commands
        return self

    def run(self, argv: tuple[str, ...], *, tier: int) -> GateResult:
        assert tier == 1
        assert argv in self.bound
        self.calls.append(argv)
        if self.unavailable:
            raise GateRunnerUnavailableError
        if self.normalize and argv[0] == "format":
            self.state = GateState(state_hash="normalized", delta_ref="ref/2", delta_sha256="diff/2")
        failed = argv[0] == self.fail
        return GateResult(
            argv=argv,
            returncode=int(failed),
            timed_out=False,
            output="heading\nerror: fix this type\n" if failed else "ok",
            log_path="log",
            duration_ms=1,
        )

    def get(self, state_hash: str, tier: str) -> MicroOutcome | None:
        return self.cache.get((state_hash, tier))

    def put(self, state_hash: str, tier: str, outcome: MicroOutcome) -> None:
        self.cache[state_hash, tier] = outcome

    def record(self, request: MicroRequest, outcome: MicroOutcome) -> None:
        assert request.node_id == outcome.receipt.node_id
        self.audit.append(outcome)

    def gate(self) -> MicroGate:
        return MicroGate(bind_runner=self.bind, capture=self.capture, cache=self, audit=self)


_PROFILE = MicroProfile(format=("format",), fix=("fix",), lint=("lint",), types=("types",), tests=("pytest",))
_REQUEST = MicroRequest(
    node_id="node",
    contract_hash="contract",
    gates_hash="gates",
    pipeline=PipelineState(step="verify", seq=4),
    changed_files=("src/b.py", "src/a.py"),
    test_files=("tests/z.py", "tests/a.py", "tests/b.py", "tests/c.py"),
)


def test_normalizes_then_checks_and_limits_sorted_one_hop_tests() -> None:
    harness = _Harness()
    harness.normalize = True
    outcome = harness.gate().run(_REQUEST, _PROFILE)
    assert [argv[0] for argv in harness.calls] == ["format", "fix", "lint", "types", "pytest"]
    assert harness.calls[0][1:] == ("src/a.py", "src/b.py")
    assert harness.calls[-1][1:] == ("tests/a.py", "tests/b.py", "tests/c.py")
    assert outcome.receipt.state.state_hash == "normalized"
    assert outcome.receipt.state.delta_ref == "ref/2"
    assert outcome.receipt.state.delta_sha256 == "diff/2"
    assert outcome.receipt.passed
    assert outcome.pipeline == _REQUEST.pipeline
    assert harness.audit == [outcome]


def test_repeat_of_normalized_state_launches_no_tools_and_does_not_reaudit() -> None:
    harness = _Harness()
    harness.normalize = True
    first = harness.gate().run(_REQUEST, _PROFILE)
    harness.calls.clear()
    second = harness.gate().run(_REQUEST, _PROFILE)
    assert second.cached
    assert second.receipt == first.receipt
    assert harness.calls == []
    assert len(harness.audit) == 1


@pytest.mark.parametrize(
    "change", ["diff", "contract", "gates", "profile", "attempt", "node", "head", "files", "tests"]
)
def test_cache_cannot_cross_state_contract_profile_or_attempt(change: str) -> None:
    harness = _Harness()
    harness.gate().run(_REQUEST, _PROFILE)
    harness.calls.clear()
    request, profile = _REQUEST, _PROFILE
    if change == "diff":
        harness.state = replace(harness.state, state_hash="changed")
    elif change == "contract":
        request = replace(request, contract_hash="changed")
    elif change == "gates":
        request = replace(request, gates_hash="changed")
    elif change == "profile":
        profile = replace(profile, types=("other-checker",))
    elif change == "attempt":
        request = replace(request, pipeline=replace(request.pipeline, attempt=2))
    elif change == "node":
        request = replace(request, node_id="other-node")
    elif change == "head":
        request = replace(request, pipeline=replace(request.pipeline, head_block_hash="new-head"))
    elif change == "files":
        request = replace(request, changed_files=("src/other.py",))
    else:
        request = replace(request, test_files=("tests/other.py",))
    assert not harness.gate().run(request, profile).cached
    assert harness.calls


@pytest.mark.parametrize("failed_tool", ["format", "fix", "lint", "types", "pytest"])
def test_failure_consumes_one_attempt_and_records_compact_actionable_verdict(failed_tool: str) -> None:
    harness = _Harness()
    harness.fail = failed_tool
    outcome = harness.gate().run(_REQUEST, _PROFILE)
    assert not outcome.receipt.passed
    assert len(outcome.receipt.compact_verdict) <= 500
    assert outcome.receipt.remaining_items == (f"{failed_tool}: error: fix this type",)
    assert outcome.pipeline.attempt == 2
    assert outcome.pipeline.step == "patch"
    assert harness.audit == [outcome]
    assert harness.calls[-1][0] == failed_tool
    harness.calls.clear()
    duplicate = harness.gate().run(_REQUEST, _PROFILE)
    assert duplicate.cached
    assert duplicate.pipeline.attempt == 2
    assert harness.calls == []
    assert len(harness.audit) == 1


def test_failure_at_limit_is_exhausted_without_starting_fifth_attempt() -> None:
    harness = _Harness()
    harness.fail = "types"
    request = replace(_REQUEST, pipeline=replace(_REQUEST.pipeline, attempt=4))
    outcome = harness.gate().run(request, _PROFILE)
    assert outcome.exhausted
    assert outcome.pipeline.attempt == 4
    assert not outcome.receipt.passed
    assert len(harness.audit) == 1


def test_unavailable_container_never_generates_receipt_or_consumes_attempt() -> None:
    harness = _Harness()
    harness.unavailable = True
    with pytest.raises(GateRunnerUnavailableError):
        harness.gate().run(_REQUEST, _PROFILE)
    assert not harness.audit
    assert not harness.cache


@pytest.mark.parametrize("path", ["../escape.py", "/outside.py", "--config=unsafe", "bad\x00.py", ""])
def test_malformed_paths_are_refused_before_launch(path: str) -> None:
    harness = _Harness()
    with pytest.raises(DomainError, match="NODE_CONTRACT"):
        harness.gate().run(replace(_REQUEST, changed_files=(path,)), _PROFILE)
    assert not harness.calls


def test_wrong_pipeline_step_is_refused() -> None:
    harness = _Harness()
    with pytest.raises(DomainError, match="STEP_OUT_OF_ORDER"):
        harness.gate().run(replace(_REQUEST, pipeline=PipelineState()), _PROFILE)
    assert not harness.calls


@pytest.mark.performance
def test_live_micro_gate_latency_without_tests(tmp_path: Path) -> None:
    """Measure the full service with real container ruff and typechecker, including Podman overhead."""
    binary = os.environ.get("ACONSOLE_TEST_PODMAN")
    container = os.environ.get("ACONSOLE_TEST_CONTAINER")
    user = os.environ.get("ACONSOLE_TEST_USER")
    tools = os.environ.get("ACONSOLE_TEST_GATE_TOOLS")
    if not binary or not container or not user or not tools:
        pytest.skip("live Podman context and gate tools directory were not supplied")
    harness = _Harness()
    source = tmp_path / "sample.py"
    source.write_text("def value() -> int:\n return 1\n")
    profile = MicroProfile(
        format=(str(Path(tools) / "ruff"), "format"),
        fix=(str(Path(tools) / "ruff"), "check", "--fix"),
        lint=(str(Path(tools) / "ruff"), "check"),
        types=(str(Path(tools) / "basedpyright"),),
        tests=("pytest",),
    )
    request = replace(_REQUEST, changed_files=("sample.py",), test_files=())

    def bind(commands: tuple[tuple[str, ...], ...]) -> GateRunner:
        return ContainerGateRunner(
            podman=Podman(Path(binary)),
            container=container,
            user=user,
            worktree=str(tmp_path),
            profile=commands,
            state_home=tmp_path,
        )

    def capture() -> GateState:
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        return GateState(state_hash=digest, delta_ref="test/snapshot", delta_sha256=digest)

    gate = MicroGate(bind_runner=bind, capture=capture, cache=harness, audit=harness)
    durations: list[float] = []
    for _ in range(5):
        harness.cache.clear()
        started = time.monotonic()
        outcome = gate.run(request, profile)
        durations.append(time.monotonic() - started)
        assert outcome.receipt.passed, outcome.receipt.compact_verdict
        assert len(outcome.receipt.results) == 4
        assert outcome.receipt.state.delta_sha256 == capture().delta_sha256
    # With five observations, the nearest-rank p95 is the maximum.
    assert max(durations) <= 5, durations
    print(f"Micro-Gate seconds: {durations}; p95={max(durations):.3f}")  # noqa: T201 -- measured acceptance evidence
