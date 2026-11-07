"""Who decides an approval. The classifier supplies tags; the policy returns the verdict."""

from typing import Protocol

from aconsole.core.domain.approval_policy import PolicyVerdict
from aconsole.core.domain.risk import RiskTag


class ApprovalPolicy(Protocol):
    """One verdict for one project. `None` uses the global choice."""

    def decide(self, project_id: str | None, tags: frozenset[RiskTag]) -> PolicyVerdict: ...
