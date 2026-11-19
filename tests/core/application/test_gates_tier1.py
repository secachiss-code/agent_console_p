"""Tier 1 normalization, cache identity, audit and retry budgets."""

# pyright: reportPrivateUsage=false
# White-box integration fixtures are shared with the MCP pipeline suite.

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
    PreflightGate,
)
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.pipeline import PipelineState
from aconsole.core.infrastructure.containers.podman import Podman
from aconsole.core.infrastructure.gates.runner import ContainerGateRunner
from aconsole.core.infrastructure.persistence.repositories.attempts import AttemptDatabase
from tests.core.test_mcp_pipeline import _PATCH
from tests.core.test_mcp_pipeline import _Harness as _PipelineHarness
from tests.core.test_mcp_pipeline import _Runner as _PipelineRunner
from tests.core.test_mcp_pipeline import pipeline as pipeline  # noqa: PLC0414 -- expose the shared pytest fixture


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
    _ = harness.gate().run(_REQUEST, _PROFILE)
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
        _ = harness.gate().run(_REQUEST, _PROFILE)
    assert not harness.audit
    assert not harness.cache


@pytest.mark.parametrize("path", ["../escape.py", "/outside.py", "--config=unsafe", "bad\x00.py", ""])
def test_malformed_paths_are_refused_before_launch(path: str) -> None:
    harness = _Harness()
    with pytest.raises(DomainError, match="NODE_CONTRACT"):
        _ = harness.gate().run(replace(_REQUEST, changed_files=(path,)), _PROFILE)
    assert not harness.calls


def test_wrong_pipeline_step_is_refused() -> None:
    harness = _Harness()
    with pytest.raises(DomainError, match="STEP_OUT_OF_ORDER"):
        _ = harness.gate().run(replace(_REQUEST, pipeline=PipelineState()), _PROFILE)
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
    _ = source.write_text("def value() -> int:\n return 1\n")
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


def test_preflight_failure_cache_retries_and_budget(pipeline: _PipelineHarness) -> None:
    board, ctx = pipeline.board, pipeline.context()
    pipeline.runner.fail = True
    for attempt in range(1, 5):
        _ = board.pipeline_step(ctx, "begin", pipeline.node, pipeline.fence)
        before = len(pipeline.runner.calls)
        reply = board.pipeline_step(
            ctx,
            "submit",
            pipeline.node,
            pipeline.fence,
            step="spec" if attempt == 1 else "patch",
            patch=_PATCH,
        ).root
        assert reply["passed"] is False
        assert reply["cached"] is (attempt > 1)
        assert reply["exhausted"] is (attempt == 4)
        verdict = reply["compact_verdict"]
        assert isinstance(verdict, str)
        assert len(verdict) < 500
        assert "\n" not in verdict
        assert "lint failed" in verdict
        assert (pipeline.repo / "src/a.py").read_text() == "VALUE = 1\n"
        if attempt > 1:
            assert len(pipeline.runner.calls) == before
        history = AttemptDatabase(pipeline.writer).attempts(pipeline.node)
        assert len(history) == attempt
        assert history[-1].status == "failed"
        assert history[-1].compact_verdict == verdict
        assert AttemptDatabase(pipeline.writer).blocks(history[-1].id) == ()
    assert board.export_plan(pipeline.plan).nodes[1].status == "blocked"


def test_preflight_changed_copy_invalidates_cache(pipeline: _PipelineHarness) -> None:
    board, ctx = pipeline.board, pipeline.context()
    pipeline.runner.fail = True
    _ = board.pipeline_step(ctx, "begin", pipeline.node, pipeline.fence)
    _ = board.pipeline_step(ctx, "submit", pipeline.node, pipeline.fence, step="spec", patch=_PATCH)
    _ = board.pipeline_step(ctx, "begin", pipeline.node, pipeline.fence)
    before = len(pipeline.runner.calls)
    pipeline.runner.fail = False
    reply = board.pipeline_step(
        ctx,
        "submit",
        pipeline.node,
        pipeline.fence,
        step="patch",
        patch=_PATCH.replace("VALUE = 2", "VALUE = 3"),
    ).root
    assert reply["passed"] is True
    assert len(pipeline.runner.calls) > before
    assert (pipeline.repo / "src/a.py").read_text().startswith("VALUE = 3\n")


def test_preflight_refusal_survives_restart_as_failed_attempt(pipeline: _PipelineHarness) -> None:
    board, ctx = pipeline.board, pipeline.context()
    pipeline.runner.fail = True
    _ = board.pipeline_step(ctx, "begin", pipeline.node, pipeline.fence)
    _ = board.pipeline_step(ctx, "submit", pipeline.node, pipeline.fence, step="spec", patch=_PATCH)
    pipeline.restart()
    history = AttemptDatabase(pipeline.writer).attempts(pipeline.node)
    assert len(history) == 1
    assert history[0].status == "failed"
    reply = pipeline.board.pipeline_step(pipeline.context(), "begin", pipeline.node, pipeline.fence).root
    assert '"attempt":2' in str(reply["handoff"])
    assert (pipeline.repo / "src/a.py").read_text() == "VALUE = 1\n"


def test_preflight_verdict_is_single_line_bounded_and_timeout_fails() -> None:
    harness = _Harness()
    gate = PreflightGate(cache=harness, audit=harness)
    result = GateResult(
        argv=("ruff",),
        returncode=0,
        timed_out=True,
        output="error: " + "x" * 2000 + "\nmore details",
        log_path="log",
        duration_ms=1,
    )
    outcome = gate.run(_REQUEST, harness.state, (("ruff",),), lambda: (result,))
    assert not outcome.receipt.passed
    assert "timeout" in outcome.receipt.compact_verdict
    assert len(outcome.receipt.compact_verdict) < 500
    assert "\n" not in outcome.receipt.compact_verdict
    result = replace(result, timed_out=False, returncode=1)
    outcome = gate.run(_REQUEST, replace(harness.state, state_hash="other"), (("ruff",),), lambda: (result,))
    assert len(outcome.receipt.compact_verdict) == 499


@pytest.mark.parametrize("change", ["state", "contract", "gates", "commands", "node"])
def test_preflight_cache_identity(change: str) -> None:
    harness = _Harness()
    gate = PreflightGate(cache=harness, audit=harness)
    result = GateResult(
        argv=("ruff",),
        returncode=1,
        timed_out=False,
        output="error: lint",
        log_path="log",
        duration_ms=1,
    )
    calls: list[bool] = []

    def execute() -> tuple[GateResult, ...]:
        calls.append(True)
        return (result,)

    _ = gate.run(_REQUEST, harness.state, (("ruff",),), execute)
    state, request, commands = harness.state, _REQUEST, (("ruff",),)
    if change == "state":
        state = replace(state, state_hash="changed")
    elif change == "contract":
        request = replace(request, contract_hash="changed")
    elif change == "gates":
        request = replace(request, gates_hash="changed")
    elif change == "node":
        request = replace(request, node_id="changed")
    else:
        commands = (("pyright",),)
    outcome = gate.run(request, state, commands, execute)
    assert not outcome.cached
    assert len(calls) == 2


def test_preflight_success_then_test_failure_leaves_reset_to_existing_port(
    pipeline: _PipelineHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board, ctx = pipeline.board, pipeline.context()
    original_run = type(pipeline.runner).run

    def fail_tests(runner: _PipelineRunner, argv: tuple[str, ...], *, tier: int) -> GateResult:
        result = original_run(runner, argv, tier=tier)
        if argv[0] == "pytest" and "--version" not in argv:
            return replace(result, returncode=1, output="test failed")
        return result

    monkeypatch.setattr(type(pipeline.runner), "run", fail_tests)
    _ = board.pipeline_step(ctx, "begin", pipeline.node, pipeline.fence)
    _ = board.pipeline_step(ctx, "submit", pipeline.node, pipeline.fence, step="spec", patch=_PATCH)
    _ = (pipeline.repo / "tests/test_a.py").write_text(
        "from src.a import VALUE\n\ndef test_value():\n    assert VALUE == 1\n"
    )
    for step in ("patch", "test"):
        _ = board.pipeline_step(ctx, "submit", pipeline.node, pipeline.fence, step=step)
    reply = board.pipeline_step(ctx, "submit", pipeline.node, pipeline.fence, step="verify").root
    assert reply["passed"] is False
    assert (pipeline.repo / "src/a.py").read_text().startswith("VALUE = 2\n")
    _ = board.pipeline_step(ctx, "begin", pipeline.node, pipeline.fence)
    _ = board.pipeline_step(ctx, "reset", pipeline.node, pipeline.fence)
    assert (pipeline.repo / "src/a.py").read_text() == "VALUE = 1\n"
