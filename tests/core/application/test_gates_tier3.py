"""Human-only direction verification, terminal children, fresh receipts and metrics."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import TYPE_CHECKING, override
from unittest.mock import patch

import pytest

from aconsole.core.application.ports.gate_runner import GateResult
from aconsole.core.application.services.gates import DirectionGate, DirectionMetrics, DirectionReceipt
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.direction_gate import DirectionRuntime
from aconsole.core.infrastructure.persistence.models import Project
from aconsole.core.infrastructure.process_metrics import UiProcesses
from tests.core.application.test_gates_tier1 import _Harness

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from tests.core.test_mcp_pipeline import _Harness as PipelineHarness
from tests.core.test_mcp_pipeline import pipeline as pipeline  # noqa: PLC0414 -- shared real database fixture

_METRICS = DirectionMetrics(
    core_rss_mib=80, ui_rss_mib=100, tab_count=5, cache_read_fraction=0.75, nodes_before=2, nodes_after=2
)
_COMMANDS = (("check",), ("pytest",))


class _DirectionHarness(_Harness):
    def __init__(self) -> None:
        super().__init__()
        self.metrics: DirectionMetrics = _METRICS
        self.receipts: list[DirectionReceipt] = []

    @override
    def run(self, argv: tuple[str, ...], *, tier: int) -> GateResult:
        assert tier == 3
        return super().run(argv, tier=1)

    def lookup(self, state: str, argv: str) -> DirectionReceipt | None:
        return next(
            (
                receipt
                for receipt in self.receipts
                if receipt.state_hash == state and receipt.argv_sha256 == argv and receipt.passed
            ),
            None,
        )

    def direction(self) -> DirectionGate:
        return DirectionGate(
            bind_runner=self.bind,
            capture=lambda: self.state.state_hash,
            measure=lambda: self.metrics,
            lookup=self.lookup,
            record=self.receipts.append,
        )


def test_only_human_with_closed_children_may_run() -> None:
    h = _DirectionHarness()
    with pytest.raises(DomainError, match="PLAN_ROLE"):
        _ = h.direction().run("direction", _COMMANDS, children_closed=True, decided_by="agent")
    with pytest.raises(DomainError, match="NODE_NOT_READY"):
        _ = h.direction().run("direction", _COMMANDS, children_closed=False, decided_by="human")
    assert h.calls == []


def test_receipt_replay_and_state_invalidation() -> None:
    h = _DirectionHarness()
    with pytest.raises(DomainError, match="GATE_RECEIPT_REQUIRED"):
        _ = h.direction().run("direction", _COMMANDS, children_closed=True, decided_by="human", check_only=True)
    receipt = h.direction().run("direction", _COMMANDS, children_closed=True, decided_by="human")
    assert receipt.passed
    assert receipt.metrics.cache_read_fraction == 0.75
    assert h.calls == list(_COMMANDS)
    assert (
        h.direction().run("direction", _COMMANDS, children_closed=True, decided_by="human", check_only=True) == receipt
    )
    assert len(h.calls) == 2
    h.state = replace(h.state, state_hash="edited")
    with pytest.raises(DomainError, match="GATE_RECEIPT_REQUIRED"):
        _ = h.direction().run("direction", _COMMANDS, children_closed=True, decided_by="human", check_only=True)


@pytest.mark.parametrize(("field", "value"), [("core_rss_mib", 121), ("ui_rss_mib", 151), ("tab_count", 4)])
def test_resource_limits_are_part_of_verdict(field: str, value: int) -> None:
    h = _DirectionHarness()
    h.metrics = replace(h.metrics, **{field: value})
    result = h.direction().run("direction", _COMMANDS, children_closed=True, decided_by="human")
    assert not result.passed
    assert h.receipts == [result]


def test_failed_check_is_not_a_direction_receipt() -> None:
    h = _DirectionHarness()
    h.fail = "check"
    result = h.direction().run("direction", _COMMANDS, children_closed=True, decided_by="human")
    assert not result.passed
    assert h.calls == [("check",)]


def test_real_direction_receipt_audit_restart_and_completion(pipeline: PipelineHarness) -> None:
    h = pipeline
    direction = h.board.export_plan(h.plan).nodes[0]
    with pytest.raises(DomainError, match="GATE_RECEIPT_REQUIRED"):
        _ = h.board.set_node_status(direction.id, "done", "Reviewed")
    # A human may cancel the module; the L0 gate still verifies the integrated checkout.
    _ = h.board.set_node_status(h.node, "cancelled", "Not needed")

    def root(session: Session) -> None:
        project = session.get(Project, "p")
        assert project is not None
        project.root_path = str(h.repo)

    _ = h.writer.call(root)
    calls: list[tuple[str, ...]] = []
    peers = UiProcesses()
    identity = peers.register(os.getpid())

    class Runner:
        def run(self, argv: tuple[str, ...], *, tier: int) -> GateResult:
            assert tier == 3
            calls.append(argv)
            return GateResult(argv=argv, returncode=0, timed_out=False, output="ok", log_path="log", duration_ms=1)

    def runtime() -> DirectionRuntime:
        return DirectionRuntime(
            book=h.board,
            writer=h.writer,
            actor=lambda _tab: h.actor,
            runner=lambda _actor, _commands: Runner(),
            tabs=lambda: (),
            accounts=lambda: (),
            ui_processes=peers,
            environment=lambda _actor, _image: "a" * 64,
        )

    with patch.object(DirectionRuntime, "_metrics", return_value=_METRICS):
        h.board.directions = runtime()
        receipt = h.board.directions.run(direction.id, "t", identity.pid)
        assert receipt.passed
        assert calls
        h.restart()
        h.board.directions = runtime()
        original = (h.repo / "src/a.py").read_text()
        _ = (h.repo / "src/a.py").write_text("VALUE = 77\n")
        with pytest.raises(DomainError, match="GATE_RECEIPT_REQUIRED"):
            _ = h.board.set_node_status(direction.id, "done", "Reviewed direction")
        _ = (h.repo / "src/a.py").write_text(original)
        assert h.board.set_node_status(direction.id, "done", "Reviewed direction").status.value == "done"


def test_direction_acceptance_with_debt_is_audited_and_requires_closed_children(pipeline: PipelineHarness) -> None:
    from sqlalchemy import select

    from aconsole.core.infrastructure.persistence.models import PlanInbox

    h = pipeline
    direction = h.board.export_plan(h.plan).nodes[0]
    with pytest.raises(DomainError, match="NODE_NOT_READY"):
        _ = h.board.set_node_status(direction.id, "done", "Accepted by user", accepted_debt="TD-1")
    with pytest.raises(DomainError, match="NODE_CONTRACT"):
        _ = h.board.set_node_status(h.node, "done", "Accepted by user", accepted_debt="TD-1")
    _ = h.board.set_node_status(h.node, "cancelled", "Not needed")
    _ = h.board.set_node_status(direction.id, "cancelled", "Awaiting decision")

    accepted = h.board.set_node_status(direction.id, "done", "Accepted by user", accepted_debt="TD-1")
    assert accepted.status.value == "done"
    h.restart()
    replayed = h.board.set_node_status(direction.id, "done", "Accepted by user", accepted_debt="TD-1")
    assert replayed.status.value == "done"

    def check_decision(session: Session) -> None:
        row = session.scalars(select(PlanInbox).where(PlanInbox.node_id == direction.id)).one()
        assert row.decided_by == "human"
        assert row.payload == {"decision": "accept_direction_debt", "decision_note": "TD-1: Accepted by user"}

    _ = h.writer.call(check_decision)


def test_sealed_environment_is_rechecked_on_replay_and_during_execution(pipeline: PipelineHarness) -> None:
    h = pipeline
    direction = h.board.export_plan(h.plan).nodes[0]
    _ = h.board.set_node_status(h.node, "cancelled", "Not needed")

    def root(session: Session) -> None:
        project = session.get(Project, "p")
        assert project is not None
        project.root_path = str(h.repo)

    _ = h.writer.call(root)
    peers = UiProcesses()
    identity = peers.register(os.getpid())
    environment = ["a" * 64]
    calls: list[tuple[str, ...]] = []
    change_during_run = [False]

    class Runner:
        def run(self, argv: tuple[str, ...], *, tier: int) -> GateResult:
            assert tier == 3
            calls.append(argv)
            if change_during_run[0]:
                environment[0] = "c" * 64
            return GateResult(argv=argv, returncode=0, timed_out=False, output="ok", log_path="log", duration_ms=1)

    runtime = DirectionRuntime(book=h.board, writer=h.writer, actor=lambda _tab: h.actor,
        runner=lambda _actor, _commands: Runner(), tabs=lambda: (), accounts=lambda: (),
        ui_processes=peers, environment=lambda _actor, _image: environment[0])
    with patch.object(DirectionRuntime, "_metrics", return_value=_METRICS):
        assert runtime.run(direction.id, "t", identity.pid).passed
        count = len(calls)
        assert runtime.run(direction.id, "t", identity.pid).passed
        assert len(calls) == count
        environment[0] = "b" * 64
        with pytest.raises(DomainError, match="GATE_RECEIPT_REQUIRED"):
            runtime.run(direction.id, "t", identity.pid, check_only=True)
        change_during_run[0] = True
        assert not runtime.run(direction.id, "t", identity.pid).passed
        environment[0] = "unsealed"
        with pytest.raises(DomainError, match="GATE_RUNNER_UNAVAILABLE"):
            runtime.run(direction.id, "t", identity.pid)
