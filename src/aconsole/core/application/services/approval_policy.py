"""Stored approval policy. Applying it to a harness is a later node."""

from typing import Protocol, final, override

from aconsole.core.application.ports.approval_policy import ApprovalPolicy
from aconsole.core.application.ports.tab_store import StoredProjects, TabDirectory
from aconsole.core.domain.approval_policy import (
    ApprovalPolicyChoice,
    PolicyOrigin,
    PolicyVerdict,
    choice_or_inherit,
    effective,
    require_ui,
    stored_choice,
    verdict,
)
from aconsole.core.domain.risk import RiskTag


class PolicyStore(Protocol):
    """The settings document and the project rows. Both outlive the process."""

    def settings(self) -> TabDirectory: ...

    def save_settings(self, settings: TabDirectory) -> None: ...

    def load_projects(self) -> StoredProjects: ...

    def save_project_policy(self, project_id: str, policy: str | None, dangerous: str | None) -> bool: ...


@final
class ApprovalPolicyBook(ApprovalPolicy):
    """Global and per-project choices. A project choice wins; a missing project inherits."""

    def __init__(self) -> None:
        self._global = ApprovalPolicyChoice()
        self._projects: dict[str, ApprovalPolicyChoice] = {}
        self._store: PolicyStore | None = None

    def attach(self, store: PolicyStore) -> None:
        """Load what the store kept. Call again after a restart with the same database."""
        self._store = store
        directory = store.settings()
        self._global = stored_choice(directory.approval_policy, directory.approval_dangerous)
        loaded = store.load_projects()
        self._projects = _projects(loaded)

    @override
    def decide(self, project_id: str | None, tags: frozenset[RiskTag]) -> PolicyVerdict:
        return verdict(self.choice_for(project_id), tags)

    def choice_for(self, project_id: str | None) -> ApprovalPolicyChoice:
        if project_id is None:
            return self._global
        return effective(self._global, self._projects.get(project_id))

    @property
    def global_choice(self) -> ApprovalPolicyChoice:
        return self._global

    def set_global(self, choice: ApprovalPolicyChoice, *, origin: PolicyOrigin) -> None:
        """Replace the global choice. MCP, ingest, and the container are refused before any write."""
        require_ui(origin)
        self._global = choice
        store = self._store
        if store is None or _same(store.settings(), choice):
            return
        current = store.settings()
        store.save_settings(_directory(current, choice, rev=current.rev + 1))

    def set_project(self, project_id: str, choice: ApprovalPolicyChoice | None, *, origin: PolicyOrigin) -> bool:
        """Replace or clear one project's override. False when the store has no such project."""
        require_ui(origin)
        store = self._store
        if store is not None and not store.save_project_policy(
            project_id,
            None if choice is None else choice.mode.value,
            None if choice is None else choice.dangerous.value,
        ):
            return False
        if choice is None:
            _ = self._projects.pop(project_id, None)
        else:
            self._projects[project_id] = choice
        return True

    def remember_global(self, choice: ApprovalPolicyChoice) -> None:
        """The settings writer already stored this. Keep the in-memory copy aligned."""
        self._global = choice

    def remember_project(self, project_id: str, choice: ApprovalPolicyChoice | None) -> None:
        """The project writer already stored this."""
        if choice is None:
            _ = self._projects.pop(project_id, None)
            return
        self._projects[project_id] = choice


def _projects(loaded: StoredProjects) -> dict[str, ApprovalPolicyChoice]:
    found: dict[str, ApprovalPolicyChoice] = {}
    for record in loaded.projects:
        choice = choice_or_inherit(record.approval_policy, record.approval_dangerous)
        if choice is not None:
            found[record.id] = choice
    return found


def _same(directory: TabDirectory, choice: ApprovalPolicyChoice) -> bool:
    return directory.approval_policy == choice.mode.value and directory.approval_dangerous == choice.dangerous.value


def _directory(current: TabDirectory, choice: ApprovalPolicyChoice, *, rev: int) -> TabDirectory:
    return TabDirectory(
        worktrees_dir=current.worktrees_dir,
        rev=rev,
        setup_completed=current.setup_completed,
        approvals_mode=current.approvals_mode,
        approval_hold_seconds=current.approval_hold_seconds,
        preferences=current.preferences,
        approval_policy=choice.mode.value,
        approval_dangerous=choice.dangerous.value,
    )
