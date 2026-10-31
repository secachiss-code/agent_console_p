"""Typed boundaries for setup diagnostics and bounded fixes."""

from dataclasses import dataclass
from typing import Protocol

from aconsole.contracts.app import CheckResult


class SetupProbe(Protocol):
    def run_checks(self) -> tuple[CheckResult, ...]: ...


@dataclass(frozen=True, kw_only=True)
class SetupFixPlan:
    fix_id: str
    summary: str
    diff: str | None
    commands: tuple[str, ...]
    reversible: bool
    source_fingerprint: str
    target_fingerprint: str


class SetupFixProvider(Protocol):
    def preview(self, fix_id: str) -> SetupFixPlan | None: ...
    def apply(self, plan: SetupFixPlan) -> CheckResult: ...


class SetupTokenSource(Protocol):
    def new_token(self) -> str: ...


class SetupFixExecutionError(Exception):
    step: str
    stderr_tail: str

    def __init__(self, *, step: str, stderr_tail: str) -> None:
        super().__init__(stderr_tail)
        self.step = step
        self.stderr_tail = stderr_tail
