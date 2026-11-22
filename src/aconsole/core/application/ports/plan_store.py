"""What a plan store keeps. The plan board in infrastructure reads and writes these records."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from aconsole.core.domain.concepts import ConceptDocument
from aconsole.core.domain.plan_admission import PlanTopology


class StoreWriteError(Exception):
    """The store did not commit a write, so the caller must not act as if it had."""


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredArtifact:
    id: str
    kind: str
    ref: str


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredNode:
    """One node with everything its plan needs after a restart."""

    id: str
    key: str
    title: str
    objective: str
    status: str
    owner: str | None
    paths: tuple[str, ...]
    acceptance: tuple[str, ...]
    review_required: bool
    boundaries: str | None
    output_format: str | None
    evidence: str | None
    note: str | None
    fence: int
    tab_id: str | None
    lease_id: str | None
    lease_expires_at: datetime | None
    layer: int
    priority: float
    unmet_blockers: int
    created_at: datetime
    depends_on: tuple[str, ...]
    parent: str | None
    related: tuple[str, ...]
    artifacts: tuple[StoredArtifact, ...]
    level: int = 1
    pipeline: bool = False
    invariants: str | None = None
    gate_profile: dict[str, object] | None = None
    test_cmd: str | None = None
    max_attempts: int = 4
    contract_sha256: str | None = None
    blast_radius: tuple[str, ...] | None = None
    baseline_children: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredPlan:
    id: str
    project_id: str
    title: str
    goal: str
    status: str
    created_at: datetime
    updated_at: datetime
    nodes: tuple[StoredNode, ...]
    topology: PlanTopology | None = None


class PlanStore(Protocol):
    def load(self) -> tuple[StoredPlan, ...]:
        """Every plan, nodes in the order they were added."""
        ...

    def save(self, plan: StoredPlan) -> None:
        """Commit the plan's new state, or raise `StoreWriteError`."""
        ...

    def delete(self, plan_id: str) -> None:
        """Remove the plan and its nodes, or raise `StoreWriteError`."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredPlanChange:
    id: str
    plan_id: str
    kind: str
    status: str
    payload: dict[str, object]
    node_id: str | None = None
    body: str = ""
    created_at: datetime | None = None
    decided_at: datetime | None = None
    decided_by: str | None = None
    tab_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredPlanDecision:
    id: str
    request_id: str | None
    node_id: str | None
    action: str
    note: str
    at: datetime
    decided_by: str = "human"


@runtime_checkable
class PlanChangeStore(PlanStore, Protocol):
    def load_change(self, change_id: str) -> StoredPlanChange | None: ...

    def save_change(self, plan: StoredPlan, change_id: str) -> None:
        """Save the plan and human decision in one transaction, only while the request is open."""
        ...


@runtime_checkable
class PlanInboxStore(PlanChangeStore, Protocol):
    def inbox(self, plan_id: str) -> tuple[StoredPlanChange, ...]: ...
    def save_attention(self, plan: StoredPlan, entry: StoredPlanChange) -> None: ...
    def save_decision(self, plan: StoredPlan, decision: StoredPlanDecision) -> None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphPreview:
    """What an add-only commit would insert at one topology revision."""

    token: str
    revision_no: int
    added: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class AssemblyReceipt:
    """The node ids a commit inserted, durable across replay and restart."""

    token: str
    plan_id: str
    revision_no: int
    node_ids: tuple[tuple[str, str], ...]


@runtime_checkable
class PlanAssemblyStore(Protocol):
    def load_assembly(self, plan_id: str, token: str) -> AssemblyReceipt | None: ...

    def save_assembly(self, plan: StoredPlan, receipt: AssemblyReceipt, expected_revision: int) -> None:
        """Commit the plan and its receipt, or raise when the topology revision moved."""
        ...


@runtime_checkable
class ConceptStore(Protocol):
    def load_concept(self, project_id: str, key: str) -> ConceptDocument | None: ...

    def save_concept(
        self,
        project_id: str,
        key: str,
        title: str,
        body: str,
        expected_revision: int,
    ) -> ConceptDocument: ...
