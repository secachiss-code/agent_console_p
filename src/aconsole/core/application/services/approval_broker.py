"""Approval broker boundary. The holding broker arrives with the adapters."""

from typing import Protocol


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
