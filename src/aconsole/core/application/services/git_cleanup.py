"""Removing a worktree whose work has landed, and the facts a merge may have outdated.

Only what is provably safe goes: no running agent, nothing uncommitted, and the branch's commits either reachable
from the base or taken by a merge in the journal. A squash leaves the originals on no branch, so they stay on a
side ref. Only branches this core made (`ac/`) are deleted; a branch the user named is theirs.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from secrets import token_hex
from typing import Final, final

from aconsole.contracts.git import (
    CleanupWorktreeParams,
    CleanupWorktreeResult,
    FactReviewView,
    FactsToReviewResult,
)
from aconsole.core.application.ports.git_repo import GitCommandError, GitWriter, WorktreeRow
from aconsole.core.application.ports.knowledge_store import StoredFact
from aconsole.core.application.services.git_merge import GitMergeService
from aconsole.core.application.services.git_ops import GitOperationService
from aconsole.core.application.services.git_state import GitStateService
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.git_operation import GitOperationRecord
from aconsole.core.domain.git_state import require_nothing_in_progress, touched_refs

_LOG: Final = logging.getLogger(__name__)
_NOT_REMOVABLE: Final = 3031
_OUR_BRANCHES: Final = "ac/"
_EXITED: Final = "exited"
_HEADS: Final = "refs/heads/"
_CACHE: Final = 256
_ID_BYTES: Final = 16

REASON_MAIN: Final = "main_checkout"
REASON_AGENT: Final = "agent_running"
REASON_DIRTY: Final = "dirty"
REASON_DETACHED: Final = "detached"
REASON_NOT_MERGED: Final = "not_merged"
REASON_IGNORED: Final = "ignored"


def _refuse(reason: str) -> DomainError:
    return DomainError(code=_NOT_REMOVABLE, name="WORKTREE_NOT_REMOVABLE", retryable=False, extra=(reason,))


@final
class GitCleanupService:
    def __init__(  # noqa: PLR0913 -- the three services it stands on, git, and what it asks of the rest
        self,
        state: GitStateService,
        operations: GitOperationService,
        merges: GitMergeService,
        writer: GitWriter,
        *,
        now: Callable[[], datetime],
        facts: Callable[[str], tuple[StoredFact, ...]],
        close_tab: Callable[[str], None],
        publish_facts: Callable[[str, tuple[FactReviewView, ...]], str | None],
        changed: Callable[[str], None],
    ) -> None:
        self._state: GitStateService = state
        self._operations: GitOperationService = operations
        self._merges: GitMergeService = merges
        self._writer: GitWriter = writer
        self._now: Callable[[], datetime] = now
        self._facts: Callable[[str], tuple[StoredFact, ...]] = facts
        self._close_tab: Callable[[str], None] = close_tab
        self._publish_facts: Callable[[str, tuple[FactReviewView, ...]], str | None] = publish_facts
        self._changed: Callable[[str], None] = changed
        self._paths: dict[tuple[str, str, str], tuple[str, ...]] = {}
        self.removed: Callable[[str, str], None] = lambda _project, _path: None

    def cleanup(self, params: CleanupWorktreeParams) -> CleanupWorktreeResult:
        project_id = str(params.project_id)
        root, row = self._state.worktree_row(project_id, params.worktree)
        with self._operations.lock_of(str(root)), self._operations.lock_of(row.path):
            kept = self._check(project_id, root, row, discard_ignored=params.discard_ignored)
            record = self._journalled(project_id, row)
            tab = self._state.tab_at(project_id, row.path)
            for tab in self._state.tabs_at(project_id, row.path):
                self._close_tab(tab.tab_id)
            removed = self._writer.remove_worktree(root, Path(row.path), discard_ignored=params.discard_ignored)
            if not removed.ok:
                self._merges.store(record.moved(self._now(), "failed", message=removed.said))
                message = "worktree was not removed"
                raise GitCommandError(message, detail=removed.said)
            self._merges.store(record.moved(self._now(), "finished"))
            deleted = False
            if row.branch is not None and row.branch.startswith(_OUR_BRANCHES):
                deleted = self._writer.delete_branch(root, row.branch).ok
            self.removed(project_id, row.path)
        self._changed(project_id)
        return CleanupWorktreeResult(removed=True, branch_deleted=deleted, kept_ref=kept)

    def facts_to_review(self, project_id: str) -> FactsToReviewResult:
        """Facts whose code changed since they were checked. Not wrong: somebody should look."""
        root = Path(self._state.project(project_id).root)
        head = self._head(project_id, root)
        if head is None:
            return FactsToReviewResult(head_oid=None, facts=())
        found: list[FactReviewView] = []
        for fact in self._facts(project_id):
            commit = fact.verified_commit
            if commit is None or commit == head:
                continue
            try:
                changed = self._changed_paths(root, commit, head)
            except GitCommandError as exc:
                # The commit may be gone after a rebase. That says nothing about the fact.
                _LOG.info("fact %s cannot be compared: %s", fact.id, exc)
                continue
            touched = touched_refs(fact.code_refs, changed)
            if touched:
                view = FactReviewView(id=fact.id, title=fact.title, verified_commit=commit, changed_paths=touched)
                found.append(view)
        return FactsToReviewResult(
            head_oid=head, facts=tuple(found), note_path=self._publish_facts(project_id, tuple(found))
        )

    def _journalled(self, project_id: str, row: WorktreeRow) -> GitOperationRecord:
        at = self._now()
        tab = self._state.tab_at(project_id, row.path)
        session = self._state.session_at(project_id, row.path)
        record = GitOperationRecord(
            id=token_hex(_ID_BYTES),
            project_id=project_id,
            work_session_id=None if session is None else session.id,
            kind="cleanup",
            status="started",
            mode=None,
            source=row.branch or "",
            target=row.path,
            source_oid=row.head or "",
            target_oid=row.head or "",
            result_oid=None,
            expected_tree=None,
            worktree=row.path,
            tab_id=None if tab is None else tab.tab_id,
            plan_node=None if tab is None else tab.plan_node,
            message=None,
            follow_up_pending=False,
            created_at=at,
            updated_at=at,
        )
        self._merges.begin(record)
        return record

    def _check(self, project_id: str, root: Path, row: WorktreeRow, *, discard_ignored: bool) -> str | None:
        """Raise unless this worktree may go. The name of the side ref its commits were kept on, if any."""
        if row.is_main:
            raise _refuse(REASON_MAIN)
        session = self._state.session_at(project_id, row.path)
        if session is not None and session.workspace_mode != "new_worktree":
            reason = "not_managed"
            raise _refuse(reason)
        members = self._state.tabs_at(project_id, row.path)
        if any(tab.status.value != _EXITED for tab in members):
            raise _refuse(REASON_AGENT)
        repo = Path(row.path)
        status = self._state.reader.status(repo)
        require_nothing_in_progress(status.in_progress)
        if status.staged + status.unstaged + status.untracked + status.conflicted:
            raise _refuse(REASON_DIRTY)
        if row.branch is None or row.head is None:
            raise _refuse(REASON_DETACHED)
        kept = self._keep_unreachable(project_id, root, row.branch, row.head)
        if not discard_ignored and self._writer.has_ignored(repo):
            raise _refuse(REASON_IGNORED)
        return kept

    def _keep_unreachable(self, project_id: str, root: Path, branch: str, head: str) -> str | None:
        base = self._head(project_id, root)
        if base is not None and self._state.reader.is_ancestor(root, head, base):
            return None
        landed = self._merges.landed_from(project_id, branch, head)
        if landed is None:
            raise _refuse(REASON_NOT_MERGED)
        prefix = (
            f"refs/aconsole/sessions/{landed.work_session_id}/merged"
            if landed.work_session_id is not None
            else "refs/aconsole/merged"
        )
        ref = f"{prefix}/{landed.id}"
        done = self._writer.keep_ref(root, ref, head)
        if not done.ok:
            message = "commits could not be kept"
            raise GitCommandError(message, detail=done.said)
        return ref

    def _head(self, project_id: str, root: Path) -> str | None:
        name = self._state.base_name(project_id)
        refs = self._state.reader.refs(root)
        return next((ref.oid for ref in refs if ref.name == f"{_HEADS}{name}"), None)

    def _changed_paths(self, root: Path, left: str, right: str) -> tuple[str, ...]:
        key = (str(root), left, right)
        found = self._paths.get(key)
        if found is None:
            found = self._state.reader.changed_paths(root, left, right)
            if len(self._paths) >= _CACHE:
                self._paths.clear()
            self._paths[key] = found
        return found
