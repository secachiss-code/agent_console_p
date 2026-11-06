"""Accept immutable worker snapshots into their own session before a final project merge."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from secrets import token_hex

import msgspec

from aconsole.contracts.app import EmptyResult
from aconsole.contracts.git import GitOperationResult, MergePreviewParams, MergeWorktreeParams
from aconsole.contracts.work_sessions import (
    WorkSessionCleanupWorkerParams,
    WorkSessionIntegrateParams,
    WorkSessionIntegratePreviewParams,
)
from aconsole.core.application.ports.git_repo import GitCommandError, GitWriter
from aconsole.core.application.services.git_merge import GitMergeService, view_of
from aconsole.core.application.services.git_ops import GitOperationService
from aconsole.core.application.services.git_state import GitStateService
from aconsole.core.application.services.work_session_history import WorkSessionHistory
from aconsole.core.application.services.work_sessions import WorkSessionService
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.git_operation import GitOperationRecord
from aconsole.core.domain.git_state import require_nothing_in_progress
from aconsole.core.domain.work_session import WorkSession, WorkSessionSnapshot


class WorkSessionGit:
    def __init__(  # noqa: PLR0913 -- application services plus the tab-worker callback
        self,
        *,
        sessions: WorkSessionService,
        history: WorkSessionHistory,
        state: GitStateService,
        operations: GitOperationService,
        merges: GitMergeService,
        writer: GitWriter,
        close_tab: Callable[[str], None],
        now: Callable[[], datetime],
    ) -> None:
        self._sessions: WorkSessionService = sessions
        self._history: WorkSessionHistory = history
        self._state: GitStateService = state
        self._operations: GitOperationService = operations
        self._merges: GitMergeService = merges
        self._writer: GitWriter = writer
        self._close_tab: Callable[[str], None] = close_tab
        self._now: Callable[[], datetime] = now

    def dispatch(self, method: str, raw: bytes) -> msgspec.Struct:
        if method == "workSession/integratePreview":
            params = msgspec.json.decode(raw, type=WorkSessionIntegratePreviewParams)
            session, snapshot = self._result(str(params.project_id), params.work_session_id, params.sequence)
            self._quiet(session)
            preview = self._merges.preview(
                MergePreviewParams(
                    project_id=params.project_id,
                    source=snapshot.ref,
                    target=session.branch,
                )
            )
            if preview.source_oid != snapshot.oid:
                raise DomainError(code=3028, name="MERGE_PREVIEW_STALE", retryable=True)
            return preview
        if method == "workSession/integrate":
            return self._integrate(msgspec.json.decode(raw, type=WorkSessionIntegrateParams))
        if method == "workSession/cleanupWorker":
            return self._cleanup(msgspec.json.decode(raw, type=WorkSessionCleanupWorkerParams))
        message = "unknown session Git method"
        raise ValueError(message)

    def _result(
        self, project_id: str, session_id: str, sequence: int, *, active: bool = True,
    ) -> tuple[WorkSession, WorkSessionSnapshot]:
        session = self._sessions.require(project_id, session_id, active=active)
        snapshot = self._history.require(session.id, sequence)
        if snapshot.worktree == session.worktree or snapshot.kind != "result":
            message = "an isolated worker result is required"
            raise ValueError(message)
        if session.branch is None:
            raise DomainError(code=3022, name="GIT_BRANCH_UNKNOWN", retryable=False)
        return session, snapshot

    def _quiet(self, session: WorkSession) -> None:
        if any(tab.status.value != "exited" for tab in self._state.tabs_at(session.project_id, session.worktree)):
            raise DomainError(code=3025, name="WORKTREE_BUSY", retryable=True)
        status = self._state.reader.status(Path(session.worktree))
        require_nothing_in_progress(status.in_progress)
        if status.staged + status.unstaged + status.untracked + status.conflicted:
            raise DomainError(code=2009, name="WORKTREE_DIRTY", retryable=False)

    def _integrate(self, params: WorkSessionIntegrateParams) -> GitOperationResult:
        session, snapshot = self._result(str(params.project_id), params.work_session_id, params.sequence)
        if params.source_oid != snapshot.oid:
            raise DomainError(code=3028, name="MERGE_PREVIEW_STALE", retryable=True)
        root = Path(self._state.project(session.project_id).root)
        with self._operations.lock_of(str(root)), self._operations.lock_of(session.worktree):
            previous = self._merges.landed_from(session.project_id, snapshot.ref, snapshot.oid or "")
            if previous is not None:
                return GitOperationResult(operation=view_of(previous))
            self._quiet(session)
            return self._merges.merge(
                MergeWorktreeParams(
                    project_id=params.project_id,
                    source=snapshot.ref,
                    target=session.branch or "",
                    source_oid=params.source_oid,
                    target_oid=params.target_oid,
                    mode="merge",
                    confirm_token=params.confirm_token,
                    message=(
                        f"Integrate worker result #{snapshot.sequence} into {session.title}"
                        f"\n\nAConsole-Session: {session.id}"
                    ),
                )
            )

    def _cleanup(self, params: WorkSessionCleanupWorkerParams) -> EmptyResult:
        session, snapshot = self._result(str(params.project_id), params.work_session_id, params.sequence, active=False)
        root, row = self._state.worktree_row(session.project_id, snapshot.worktree)
        with self._operations.lock_of(str(root)), self._operations.lock_of(row.path):
            if self._merges.landed_from(session.project_id, snapshot.ref, snapshot.oid or "") is None:
                raise DomainError(code=3031, name="WORKTREE_NOT_REMOVABLE", retryable=False, extra=("not_merged",))
            if row.head != snapshot.head_oid:
                raise DomainError(code=3031, name="WORKTREE_NOT_REMOVABLE", retryable=False, extra=("not_merged",))
            members = self._state.tabs_at(session.project_id, row.path)
            if any(tab.status.value != "exited" for tab in members):
                raise DomainError(code=3025, name="WORKTREE_BUSY", retryable=True)
            status = self._state.reader.status(Path(row.path))
            require_nothing_in_progress(status.in_progress)
            if status.staged + status.unstaged + status.untracked + status.conflicted:
                raise DomainError(code=3031, name="WORKTREE_NOT_REMOVABLE", retryable=False, extra=("dirty",))
            if not params.discard_ignored and self._writer.has_ignored(Path(row.path)):
                raise DomainError(code=3031, name="WORKTREE_NOT_REMOVABLE", retryable=False, extra=("ignored",))
            now = self._now()
            record = GitOperationRecord(
                id=token_hex(16),
                project_id=session.project_id,
                work_session_id=session.id,
                kind="cleanup",
                status="started",
                mode=None,
                source=snapshot.ref,
                target=row.path,
                source_oid=snapshot.oid or "",
                target_oid=row.head or "",
                result_oid=None,
                expected_tree=snapshot.tree,
                worktree=row.path,
                tab_id=snapshot.actor_tab_id,
                plan_node=snapshot.plan_node,
                message=None,
                follow_up_pending=False,
                created_at=now,
                updated_at=now,
            )
            self._merges.begin(record)
            try:
                for tab in members:
                    self._close_tab(tab.tab_id)
                self._remove(root, Path(row.path), discard_ignored=params.discard_ignored)
            except Exception as exc:
                self._merges.store(record.moved(self._now(), "failed", message=str(exc)))
                raise
            self._merges.store(record.moved(self._now(), "finished"))
        return EmptyResult()

    def _remove(self, root: Path, path: Path, *, discard_ignored: bool) -> None:
        done = self._writer.remove_worktree(root, path, discard_ignored=discard_ignored)
        if not done.ok:
            message = "worker worktree was not removed"
            raise GitCommandError(message, detail=done.said)
