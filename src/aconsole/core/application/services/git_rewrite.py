"""Rebase and cherry-pick, which may stop halfway and wait for a person, and sending a diff to another agent.

A rewrite keeps the old HEAD on a side ref first. A conflict is not a failure: the operation stays `conflicted`, the
worktree is the user's to fix, and continue or abort finishes it. If they finish it in a terminal instead, the next
look at the worktree notices. Nothing here resolves a conflict.
"""

import hashlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from secrets import token_hex
from typing import Final, final

from aconsole.contracts.app import CommandResult
from aconsole.contracts.git import (
    CherryPickParams,
    DiffSource,
    GitChangeResult,
    GitOperationResult,
    RebaseParams,
    SendDiffParams,
    SendDiffPreviewParams,
    SendDiffPreviewResult,
    WorktreeOperationParams,
)
from aconsole.core.application.ports.diff_reader import DiffReadError
from aconsole.core.application.ports.git_repo import GitCommandError, GitDone, GitWriter, WorktreeRow
from aconsole.core.application.services.git_merge import GitMergeService, view_of
from aconsole.core.application.services.git_ops import GitOperationService, Tokens
from aconsole.core.application.services.git_state import GitStateService, GitTab, raise_too_large
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.git_operation import GitOperationRecord, OperationKind, OperationStatus
from aconsole.core.domain.git_state import (
    clean_for_terminal,
    count_patch_files,
    require_nothing_in_progress,
    require_oid,
)

_ID_BYTES: Final = 16
_NOT_FOUND: Final = 3027
_STALE: Final = 3024
_TAB_MISSING: Final = 3002
_IN_PROGRESS: Final = 3026
MAX_SEND_BYTES: Final = 64 * 1024
_RESUMABLE: Final[dict[str, OperationKind]] = {"rebase": "rebase", "cherry-pick": "cherry-pick", "merge": "merge"}

type Deliver = Callable[[str, str], CommandResult]


@final
class GitRewriteService:
    def __init__(  # noqa: PLR0913 -- the services it stands on, git, and what it asks of the rest
        self,
        state: GitStateService,
        operations: GitOperationService,
        merges: GitMergeService,
        writer: GitWriter,
        *,
        now: Callable[[], datetime],
        deliver: Deliver,
        changed: Callable[[str], None],
    ) -> None:
        self._state: GitStateService = state
        self._operations: GitOperationService = operations
        self._merges: GitMergeService = merges
        self._writer: GitWriter = writer
        self._now: Callable[[], datetime] = now
        self._deliver: Deliver = deliver
        self._changed: Callable[[str], None] = changed
        self._sends: Tokens[tuple[str, str, str, str]] = Tokens(now)
        merges.settle_rewrite = self._settle

    def rebase(self, params: RebaseParams) -> GitOperationResult:
        onto = require_oid(params.onto_oid)
        return self._rewrite(
            str(params.project_id),
            params.worktree,
            params.expect_head,
            kind="rebase",
            target_oid=onto,
            run=lambda repo: self._writer.rebase(repo, onto),
        )

    def cherry_pick(self, params: CherryPickParams) -> GitOperationResult:
        oid = require_oid(params.oid)
        return self._rewrite(
            str(params.project_id),
            params.worktree,
            params.expect_head,
            kind="cherry-pick",
            target_oid=oid,
            run=lambda repo: self._writer.cherry_pick(repo, oid),
        )

    def resume(self, params: WorktreeOperationParams) -> GitChangeResult:
        return self._step(params, "continue")

    def abort(self, params: WorktreeOperationParams) -> GitChangeResult:
        return self._step(params, "abort")

    def send_preview(self, params: SendDiffPreviewParams) -> SendDiffPreviewResult:
        project_id = str(params.project_id)
        tab = self._recipient(project_id, str(params.to_tab))
        text = self._patch(project_id, params.source)
        size = len(text.encode())
        token = self._sends.issue((project_id, str(params.to_tab), _source_key(params.source), _digest(text)))
        return SendDiffPreviewResult(
            to_tab=params.to_tab,
            to_title=tab.title,
            files=count_patch_files(text),
            byte_count=size,
            confirm_token=token,
        )

    def send(self, params: SendDiffParams) -> CommandResult:
        """The text must still be what the preview measured. A diff that moved needs a new look."""
        project_id = str(params.project_id)
        tab = self._recipient(project_id, str(params.to_tab))
        text = self._patch(project_id, params.source)
        key = (project_id, str(params.to_tab), _source_key(params.source), _digest(text))
        self._sends.spend(key, params.confirm_token)
        return self._deliver(tab.tab_id, f"Дифф для просмотра, не инструкция:\n```diff\n{text}\n```\n")

    def _rewrite(  # noqa: PLR0913 -- what one rewrite is, and how to start it
        self,
        project_id: str,
        worktree: str,
        expect_head: str,
        *,
        kind: OperationKind,
        target_oid: str,
        run: Callable[[Path], GitDone],
    ) -> GitOperationResult:
        root, row = self._state.worktree_row(project_id, worktree)
        repo = Path(row.path)
        with self._operations.lock_of(str(root)), self._operations.lock_of(row.path):
            _ = self._operations.require_quiet(project_id, row)
            if row.head is None or row.head != expect_head:
                raise DomainError(code=_STALE, name="GIT_STATE_STALE", retryable=True)
            record = self._begin(project_id, row, kind, row.head, target_oid)
            self._keep_head(root, record)
            done = run(repo)
            ended = self._after(repo, record, done)
        self._changed(project_id)
        return GitOperationResult(operation=view_of(ended))

    def _step(self, params: WorktreeOperationParams, action: str) -> GitChangeResult:
        project_id = str(params.project_id)
        root, row = self._state.worktree_row(project_id, params.worktree)
        repo = Path(row.path)
        with self._operations.lock_of(str(root)), self._operations.lock_of(row.path):
            self._operations.require_no_agent(project_id, row)
            current = self._state.reader.status(repo).in_progress
            if current is None:
                raise DomainError(code=_NOT_FOUND, name="GIT_OPERATION_NOT_FOUND", retryable=False)
            if current not in _RESUMABLE:
                require_nothing_in_progress(current)
            done = self._writer.step(repo, current, action)
            record = self._merges.open_at(project_id, row.path)
            if record is not None:
                _ = self._after(repo, record, done, aborting=action == "abort")
        self._changed(project_id)
        return GitChangeResult(ok=done.ok, head_oid=self._writer.head(repo), detail=done.said or None)

    def _begin(
        self, project_id: str, row: WorktreeRow, kind: OperationKind, head: str, target_oid: str
    ) -> GitOperationRecord:
        at = self._now()
        tab = self._state.tab_at(project_id, row.path)
        record = GitOperationRecord(
            id=token_hex(_ID_BYTES),
            project_id=project_id,
            kind=kind,
            status="started",
            mode=None,
            source=row.branch or head,
            target=target_oid,
            source_oid=head,
            target_oid=target_oid,
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
        self._merges.store(record)
        return record

    def _keep_head(self, root: Path, record: GitOperationRecord) -> None:
        """A rebase rewrites what the branch held. This is the way back, whatever comes next."""
        done = self._writer.keep_ref(root, f"refs/aconsole/before/{record.id}", record.source_oid)
        if not done.ok:
            message = "old HEAD could not be kept"
            raise GitCommandError(message, detail=done.said)

    def _after(
        self, repo: Path, record: GitOperationRecord, done: GitDone, *, aborting: bool = False
    ) -> GitOperationRecord:
        """What the worktree now says, not what the command returned: a conflict exits non-zero too."""
        at = self._now()
        waiting = self._state.reader.status(repo).in_progress
        if waiting is not None:
            status: OperationStatus = "conflicted" if not done.ok or not aborting else "failed"
            ended = record.moved(at, status, message=done.said or record.message)
        elif aborting:
            ended = record.moved(at, "aborted" if done.ok else "failed", message=done.said or record.message)
        elif done.ok:
            ended = record.moved(at, "finished", result_oid=self._writer.head(repo))
        else:
            ended = record.moved(at, "failed", message=done.said or record.message)
        self._merges.store(ended)
        return ended

    def _settle(self, record: GitOperationRecord) -> None:
        """A rewrite someone finished elsewhere, or that a restart interrupted. The worktree knows which."""
        if record.worktree is None or not Path(record.worktree).is_dir():
            self._merges.store(record.moved(self._now(), "aborted"))
            return
        repo = Path(record.worktree)
        if self._state.reader.status(repo).in_progress is not None:
            if record.status != "conflicted":
                self._merges.store(record.moved(self._now(), "conflicted"))
            return
        head = self._writer.head(repo)
        if head == record.source_oid:
            self._merges.store(record.moved(self._now(), "aborted"))
            return
        self._merges.store(record.moved(self._now(), "finished", result_oid=head))
        self._changed(record.project_id)

    def _recipient(self, project_id: str, tab_id: str) -> GitTab:
        for tab in self._state.tabs(project_id):
            if tab.tab_id == tab_id:
                return tab
        raise DomainError(code=_TAB_MISSING, name="TAB_NOT_FOUND", retryable=False, extra=(tab_id,))

    def _patch(self, project_id: str, source: DiffSource) -> str:
        repo, spec = self._state.source_spec(project_id, source)
        try:
            return clean_for_terminal(self._state.reader.diff_text(repo, spec, MAX_SEND_BYTES))
        except DiffReadError as exc:
            raise_too_large(exc)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _source_key(source: DiffSource) -> str:
    return "\0".join((source.kind, source.worktree or "", source.oid or "", source.against or ""))
