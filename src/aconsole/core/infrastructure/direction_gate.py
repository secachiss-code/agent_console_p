"""Human Tier 3 adapter: frozen commands, actual process metrics and durable audit."""

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Protocol, final
from uuid import uuid4

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from aconsole.contracts.app import AccountView, TabView
from aconsole.core.application.ports.gate_runner import GateRunner, GateRunnerUnavailableError
from aconsole.core.application.ports.plan_store import StoredNode, StoredPlan
from aconsole.core.application.services.gates import DirectionGate, DirectionMetrics, DirectionReceipt
from aconsole.core.application.services.pipeline import PipelineActor
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.git.checkpoints import GitCheckpoints
from aconsole.core.infrastructure.persistence.models import GateReceipt, Plan, PlanEdge, PlanInbox, PlanNode, Project
from aconsole.core.infrastructure.persistence.writer import Writer

_DEFAULT = (("sh", "tools/check.sh"), (".venv-ui/bin/python", "-m", "pytest", "tests"))
_KIB_IN_MIB = 1024
_MODULE_ARGV = 3


class DirectionBook(Protocol):
    def export_plan(self, plan_id: str) -> StoredPlan: ...
    def plan_ids(self) -> tuple[str, ...]: ...


def process_rss(pid: int) -> float:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / _KIB_IN_MIB
    except (OSError, ValueError) as exc:
        raise DomainError(code=5010, name="GATE_RUNNER_UNAVAILABLE", retryable=True) from exc
    raise DomainError(code=5010, name="GATE_RUNNER_UNAVAILABLE", retryable=True)


@final
class DirectionRuntime:
    def __init__(  # noqa: PLR0913 -- explicit application and execution ports
        self,
        *,
        book: DirectionBook,
        writer: Writer,
        actor: Callable[[str], PipelineActor],
        runner: Callable[[PipelineActor, tuple[tuple[str, ...], ...]], GateRunner],
        tabs: Callable[[], tuple[TabView, ...]],
        accounts: Callable[[], tuple[AccountView, ...]],
    ) -> None:
        self.book, self.writer, self.actor, self.runner = book, writer, actor, runner
        self.tabs, self.accounts = tabs, accounts
        self._lock = RLock()
        self._checkpoints = GitCheckpoints()
        self._versions: dict[tuple[str, tuple[tuple[str, ...], ...]], str] = {}

    def run(
        self,
        node_id: str,
        tab_id: str | None,
        ui_pid: int | None,
        *,
        check_only: bool = False,
        decided_by: str = "human",
    ) -> DirectionReceipt:
        if decided_by != "human":
            raise DomainError(code=4012, name="PLAN_ROLE", retryable=False)
        with self._lock:
            plan, direction = self._find(node_id)
            if tab_id is None or ui_pid is None:
                tab_id, ui_pid = self._previous(node_id)
            if any(node.status not in {"done", "cancelled"} for node in plan.nodes if node.parent == direction.key):
                raise DomainError(code=4003, name="NODE_NOT_READY", retryable=False)
            actor = self.actor(tab_id)
            self._require_root(actor, plan)
            try:
                commands = TypeAdapter(tuple[tuple[str, ...], ...]).validate_python(
                    (direction.gate_profile or {}).get("direction", _DEFAULT)
                )
            except ValueError as exc:
                raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False) from exc
            versions = self._tool_versions(actor, commands)
            gate = DirectionGate(
                bind_runner=lambda argv: self.runner(actor, argv),
                capture=lambda: self._capture(actor, plan, direction, versions),
                measure=lambda: self._metrics(actor, ui_pid, plan),
                lookup=lambda state, argv: self._lookup(
                    node_id, state, argv, metrics=self._metrics(actor, ui_pid, plan)
                ),
                record=lambda receipt: self._record(plan, direction, actor, ui_pid, receipt),
            )
            closed = all(node.status in {"done", "cancelled"} for node in plan.nodes if node.parent == direction.key)
            try:
                return gate.run(node_id, commands, children_closed=closed, decided_by=decided_by, check_only=check_only)
            except GateRunnerUnavailableError as exc:
                raise DomainError(code=5010, name="GATE_RUNNER_UNAVAILABLE", retryable=True) from exc

    def _find(self, node_id: str) -> tuple[StoredPlan, StoredNode]:
        for plan_id in self.book.plan_ids():
            plan = self.book.export_plan(plan_id)
            direction = next((node for node in plan.nodes if node.id == node_id and node.level == 0), None)
            if direction is not None:
                if plan.topology is None or plan.topology.phase != "frozen" or not direction.contract_sha256:
                    raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
                return plan, direction
        raise DomainError(code=4002, name="NODE_NOT_FOUND", retryable=False)

    def _previous(self, node_id: str) -> tuple[str, int]:
        found: list[tuple[str, int]] = []

        def read(session: Session) -> None:
            rows = session.scalars(
                select(PlanInbox)
                .where(PlanInbox.node_id == node_id, PlanInbox.kind == "human", PlanInbox.decided_by == "human")
                .order_by(PlanInbox.created_at.desc())
            )
            for row in rows:
                payload = row.payload
                tab, pid = payload.get("tab_id"), payload.get("ui_pid")
                if payload.get("action") == "direction_gate" and isinstance(tab, str) and isinstance(pid, int):
                    found.append((tab, pid))
                    break

        _ = self.writer.call(read)
        if not found:
            raise DomainError(code=4018, name="GATE_RECEIPT_REQUIRED", retryable=False)
        return found[0]

    def _require_root(self, actor: PipelineActor, plan: StoredPlan) -> None:
        roots: list[str] = []

        def read(session: Session) -> None:
            project = session.get(Project, plan.project_id)
            if project is not None:
                roots.append(project.root_path)

        _ = self.writer.call(read)
        if actor.project_id != plan.project_id or not roots or actor.worktree.resolve() != Path(roots[0]).resolve():
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)

    def _tool_versions(self, actor: PipelineActor, commands: tuple[tuple[str, ...], ...]) -> str:
        key = (actor.tab_id, commands)
        if key not in self._versions:
            probes = tuple(sorted({_version_command(argv) for argv in commands}))
            runner = self.runner(actor, probes)
            observed = tuple(
                (result.returncode, result.output) for result in (runner.run(argv, tier=3) for argv in probes)
            )
            self._versions[key] = hashlib.sha256(json.dumps(observed).encode()).hexdigest()
        return self._versions[key]

    def _capture(self, actor: PipelineActor, plan: StoredPlan, direction: StoredNode, versions: str) -> str:
        current, fresh = self._find(direction.id)
        ref = f"refs/aconsole/directions/{direction.id}/{uuid4().hex}"
        try:
            tree = self._checkpoints.capture(actor.worktree, ref, "Direction gate snapshot").tree
        finally:
            self._checkpoints.delete(actor.worktree, ref)
        children = tuple(
            (node.id, node.status, node.contract_sha256) for node in current.nodes if node.parent == fresh.key
        )
        topology = current.topology
        body = (
            tree,
            plan.id,
            None if topology is None else topology.revision_no,
            None if topology is None else topology.gates_sha256,
            fresh.contract_sha256,
            sorted(children),
            versions,
        )
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    def _metrics(self, actor: PipelineActor, ui_pid: int, plan: StoredPlan) -> DirectionMetrics:
        if ui_pid <= 0 or ui_pid == os.getpid():
            raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
        inputs = cached = 0
        for account in self.accounts():
            for usage in account.usage:
                cached += usage.cache_read_tokens
                inputs += usage.input_tokens
                inputs += usage.cache_read_tokens + usage.cache_write_tokens
        return DirectionMetrics(
            core_rss_mib=process_rss(os.getpid()),
            ui_rss_mib=process_rss(ui_pid),
            tab_count=sum(tab.project_id == actor.project_id and tab.status.value != "exited" for tab in self.tabs()),
            cache_read_fraction=cached / inputs if inputs else None,
            nodes_before=0 if plan.topology is None else plan.topology.baseline_nodes,
            nodes_after=len(plan.nodes),
        )

    def _lookup(self, node_id: str, state: str, argv: str, *, metrics: DirectionMetrics) -> DirectionReceipt | None:
        found: list[DirectionReceipt] = []

        def read(session: Session) -> None:
            row = session.scalar(
                select(GateReceipt)
                .where(
                    GateReceipt.node_id == node_id,
                    GateReceipt.tier == "direction",
                    GateReceipt.state_hash == state,
                    GateReceipt.argv_sha256 == argv,
                    GateReceipt.passed.is_(True),
                )
                .order_by(GateReceipt.created_at.desc())
                .limit(1)
            )
            if row is not None:
                found.append(
                    DirectionReceipt(
                        node_id=node_id,
                        state_hash=state,
                        argv_sha256=argv,
                        passed=True,
                        verdict=row.compact_verdict,
                        metrics=metrics,
                    )
                )

        _ = self.writer.call(read)
        return found[0] if found else None

    def _record(
        self, plan: StoredPlan, direction: StoredNode, actor: PipelineActor, ui_pid: int, receipt: DirectionReceipt
    ) -> None:
        def write(session: Session) -> None:
            stored, node = session.get(Plan, plan.id), session.get(PlanNode, direction.id)
            children = session.scalars(
                select(PlanNode)
                .join(PlanEdge, PlanEdge.dst_node_id == PlanNode.id)
                .where(PlanEdge.src_node_id == direction.id, PlanEdge.kind == "parent")
            )
            if (
                stored is None
                or stored.phase != "frozen"
                or plan.topology is None
                or stored.revision_no != plan.topology.revision_no
                or stored.gates_sha256 != plan.topology.gates_sha256
                or node is None
                or node.contract_sha256 != direction.contract_sha256
                or any(child.status.value not in {"done", "cancelled"} for child in children)
            ):
                raise DomainError(code=4003, name="NODE_NOT_READY", retryable=False)
            now = datetime.now(UTC)
            if session.get(GateReceipt, receipt.receipt_hash) is None:
                session.add(
                    GateReceipt(
                        receipt_hash=receipt.receipt_hash,
                        node_id=direction.id,
                        tier="direction",
                        state_hash=receipt.state_hash,
                        argv_sha256=receipt.argv_sha256,
                        passed=receipt.passed,
                        exit_code=next(
                            (result.returncode for result in receipt.results if result.returncode),
                            int(not receipt.passed),
                        ),
                        compact_verdict=receipt.verdict,
                        log_ref=receipt.results[-1].log_path if receipt.results else None,
                        duration_ms=sum(result.duration_ms for result in receipt.results),
                        created_at=now,
                    )
                )
            session.add(
                PlanInbox(
                    id=uuid4().hex[:26],
                    plan_id=plan.id,
                    node_id=direction.id,
                    kind="human",
                    status="applied",
                    body=receipt.verdict,
                    created_at=now,
                    decided_at=now,
                    decided_by="human",
                    payload={
                        "action": "direction_gate",
                        "tab_id": actor.tab_id,
                        "ui_pid": ui_pid,
                        "receipt_hash": receipt.receipt_hash,
                        "metrics": asdict(receipt.metrics),
                    },
                )
            )

        _ = self.writer.call(write)


def _version_command(argv: tuple[str, ...]) -> tuple[str, ...]:
    if not argv:
        raise DomainError(code=4013, name="NODE_CONTRACT", retryable=False)
    if len(argv) >= _MODULE_ARGV and argv[1] == "-m":
        return (*argv[:_MODULE_ARGV], "--version")
    return (argv[0], "--version")
