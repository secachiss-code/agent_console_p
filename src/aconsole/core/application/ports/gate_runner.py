"""Container-only execution of commands selected from a frozen gate profile."""

from dataclasses import dataclass
from typing import Protocol


class GateRunnerUnavailableError(Exception):
    """GATE_RUNNER_UNAVAILABLE: execution could not be established."""


@dataclass(frozen=True, kw_only=True)
class GateResult:
    argv: tuple[str, ...]
    returncode: int
    timed_out: bool
    output: str
    log_path: str
    duration_ms: int


class GateRunner(Protocol):
    def run(self, argv: tuple[str, ...], *, tier: int) -> GateResult:
        """Run an exact profile command in the tab's container worktree."""
        ...
