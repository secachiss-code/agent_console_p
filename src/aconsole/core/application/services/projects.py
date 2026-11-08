"""The project list: `project/add`, `project/remove`, `project/setActive`, and what the snapshot shows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, Protocol, final

from aconsole.contracts.app import ProjectView
from aconsole.contracts.common import ProjectId
from aconsole.core.application.ports.tab_store import ProjectRecord, ProjectStore, StoredProjects
from aconsole.core.domain.approval_policy import ApprovalPolicyChoice, PolicyOrigin, require_ui

_NAME: Final = 120


class RepoInspector(Protocol):
    def toplevel(self, path: Path) -> tuple[Path, str] | None:
        """The repository root and its branch, or None when `path` is not inside a git repository."""
        ...


@final
class ProjectCatalog:
    """Projects by id. Idempotent by root, as APP_API §4.1 asks."""

    def __init__(
        self,
        inspector: RepoInspector,
        *,
        new_id: Callable[[], str],
        now: Callable[[], datetime],
    ) -> None:
        self._inspector = inspector
        self._new_id = new_id
        self._now = now
        self._store: ProjectStore | None = None
        self._records: dict[str, ProjectRecord] = {}
        self._active: str | None = None

    def attach(self, store: ProjectStore) -> tuple[ProjectRecord, ...]:
        """Use `store` from now on and load what it holds."""
        self._store = store
        found: StoredProjects = store.load_projects()
        self._records = {record.id: record for record in found.projects}
        self._active = found.active if found.active in self._records else None
        return found.projects

    def add(self, root: str) -> ProjectRecord | None:
        """The record for `root`. None when it is not a git repository."""
        located = self._inspector.toplevel(Path(root).expanduser())
        if located is None:
            return None
        top, branch = located
        for record in self._records.values():
            if record.root == str(top):
                return record
        record = ProjectRecord(
            id=self._new_id(),
            name=top.name[:_NAME] or str(top)[:_NAME],
            root=str(top),
            default_branch=branch,
            created_at=self._now(),
        )
        self._records[record.id] = record
        if self._store is not None:
            self._store.save_project(record)
        if self._active is None:
            self.set_active(record.id)
        return record

    def known(self, project_id: str) -> bool:
        return project_id in self._records

    def remove(self, project_id: str) -> None:
        del self._records[project_id]
        if self._store is not None:
            self._store.remove_project(project_id)
        if self._active == project_id:
            self.set_active(next(iter(self._records), None))

    def set_active(self, project_id: str | None) -> None:
        self._active = project_id
        if self._store is not None:
            self._store.save_active_project(project_id)

    def set_approval_policy(
        self,
        project_id: str,
        choice: ApprovalPolicyChoice | None,
        *,
        origin: PolicyOrigin,
    ) -> ProjectRecord:
        """Replace or clear the override. Only the UI may call this."""
        require_ui(origin)
        current = self._records[project_id]
        updated = replace(
            current,
            approval_policy=None if choice is None else choice.mode.value,
            approval_dangerous=None if choice is None else choice.dangerous.value,
        )
        if self._store is not None and not self._store.save_project_policy(
            project_id,
            updated.approval_policy,
            updated.approval_dangerous,
        ):
            raise KeyError(project_id)
        self._records[project_id] = updated
        return updated

    @property
    def active(self) -> ProjectId | None:
        return None if self._active is None else ProjectId(self._active)

    def views(self) -> tuple[ProjectView, ...]:
        ordered = sorted(self._records.values(), key=lambda record: (record.created_at, record.id))
        return tuple(
            ProjectView(
                id=ProjectId(record.id),
                name=record.name,
                root=record.root,
                default_branch=record.default_branch,
                vault_folder=None,
                languages=frozenset(),
                approval_policy=_policy_mode(record.approval_policy),
                approval_dangerous=_dangerous_mode(record.approval_policy, record.approval_dangerous),
            )
            for record in ordered
        )


_POLICY_VALUES: dict[str, Literal["ask", "allow", "deny", "scenario"]] = {
    "ask": "ask",
    "allow": "allow",
    "deny": "deny",
    "scenario": "scenario",
}


def _policy_mode(value: str | None) -> Literal["ask", "allow", "deny", "scenario"] | None:
    if value is None:
        return None
    return _POLICY_VALUES.get(value)


def _dangerous_mode(policy: str | None, value: str | None) -> Literal["ask", "allow"] | None:
    """A dangerous submode without a mode is not an override."""
    if _policy_mode(policy) is None:
        return None
    if value == "allow":
        return "allow"
    return "ask"
