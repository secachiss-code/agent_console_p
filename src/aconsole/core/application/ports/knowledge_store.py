"""What a knowledge store keeps. The plan board in infrastructure reads and writes these records."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredFact:
    id: str
    project_id: str
    kind: str
    title: str
    body: str
    source: str
    code_refs: tuple[str, ...]
    stale: bool
    note_ref: str
    updated_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredHandover:
    id: str
    project_id: str
    tab_id: str | None
    note_ref: str
    goal: str
    done: tuple[str, ...]
    decisions: tuple[str, ...]
    open_questions: tuple[str, ...]
    next_steps: tuple[str, ...]
    failing_checks: tuple[str, ...]
    created_at: datetime


class KnowledgeStore(Protocol):
    def load(self) -> tuple[tuple[StoredFact, ...], tuple[StoredHandover, ...]]:
        """Every fact and handover, oldest first."""
        ...

    def save_fact(self, fact: StoredFact, supersedes: str | None) -> None:
        """Commit a new fact and mark the one it replaces stale, or raise `StoreWriteError`."""
        ...

    def save_handover(self, packet: StoredHandover) -> None:
        """Commit a handover packet, or raise `StoreWriteError`."""
        ...
