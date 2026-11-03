"""What a plan store keeps. The plan board in infrastructure reads and writes these records."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


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


class PlanStore(Protocol):
    def load(self) -> tuple[StoredPlan, ...]:
        """Every plan, nodes in the order they were added."""
        ...

    def save(self, plan: StoredPlan) -> None:
        """Commit the plan's new state, or raise `StoreWriteError`."""
        ...
