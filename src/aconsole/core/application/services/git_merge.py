"""Merging one branch into another, exactly as previewed.

A preview is good for one pair of commits. Before anything moves both branches are read again, and if either
moved the user must look again. Every merge is journalled before it starts, so after a crash a restart can tell
from the branch itself whether it landed. What follows a merge (the plan, for one) runs after it and is retried:
the merge is already true by then and nothing undoes it.
"""

import logging
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from secrets import token_hex
from typing import Final, final

from aconsole.contracts.common import NodeId, TabId
from aconsole.contracts.git import (
    GitOperationParams,
    GitOperationResult,
    GitOperationView,
    MergePreviewParams,
    MergePreviewResult,
    MergeWorktreeParams,
)
from aconsole.core.application.ports.git_repo import (
    GitCommandError,
    GitDone,
    GitOperationJournal,
    GitWriter,
    RefRow,
    WorktreeRow,
)
from aconsole.core.application.ports.plan_store import StoreWriteError
from aconsole.core.application.services.git_ops import GitOperationService, Tokens
from aconsole.core.application.services.git_state import GitStateService
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.git_operation import (
    INTERRUPTED,
    TARGET_MOVED,
    GitOperationRecord,
    MergeMode,
    landed,
    merge_modes,
)
from aconsole.core.domain.git_state import blocks_writes, require_oid
from aconsole.core.domain.tab_status import TabStatus

_LOG: Final = logging.getLogger(__name__)
_HEADS: Final = "refs/heads/"
_ID_BYTES: Final = 16
_BRANCH_UNKNOWN: Final = 3022
_NOT_FOUND: Final = 3027
_STALE: Final = 3028
_CONFLICT: Final = 3029
_MODE: Final = 3030
_BUSY: Final = 3025
_IN_PROGRESS: Final = 3026
_NO_JOURNAL: Final = 3032
_REWRITES: Final = frozenset({"rebase", "cherry-pick"})

BLOCK_CONFLICT: Final = "conflict"
BLOCK_MERGED: Final = "already_merged"
BLOCK_SAME: Final = "same_branch"
BLOCK_SOURCE_BUSY: Final = "source_busy"
BLOCK_TARGET_BUSY: Final = "target_busy"
BLOCK_TARGET_DIRTY: Final = "target_dirty"
BLOCK_IN_PROGRESS: Final = "target_in_progress"
BLOCK_SOURCE_DIRTY: Final = "source_dirty"

type FollowUp = Callable[[GitOperationRecord], None]


def view_of(record: GitOperationRecord) -> GitOperationView:
    return GitOperationView(
        id=record.id,
        work_session_id=record.work_session_id,
        kind=record.kind,
        status=record.status,
        mode=record.mode,
        source=record.source,
        target=record.target,
        source_oid=record.source_oid,
        target_oid=record.target_oid,
        result_oid=record.result_oid,
        tab_id=None if record.tab_id is None else TabId(record.tab_id),
        plan_node=None if record.plan_node is None else NodeId(record.plan_node),
        message=record.message,
        follow_up_pending=record.follow_up_pending,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _oid_of(refs: tuple[RefRow, ...], branch: str) -> str:
    wanted = branch if branch.startswith("refs/aconsole/sessions/") else f"{_HEADS}{branch}"
    for ref in refs:
        if ref.name == wanted:
            return ref.oid
    raise DomainError(code=_BRANCH_UNKNOWN, name="GIT_BRANCH_UNKNOWN", retryable=False, extra=(branch,))


def _row_on(rows: tuple[WorktreeRow, ...], branch: str) -> WorktreeRow | None:
    return next((row for row in rows if row.branch == branch), None)


def _refusal(blockers: tuple[str, ...]) -> DomainError | None:
    """The first reason, as the error it means. The preview listed them all."""
    for blocker in blockers:
        if blocker in {BLOCK_SOURCE_BUSY, BLOCK_TARGET_BUSY}:
            return DomainError(code=_BUSY, name="WORKTREE_BUSY", retryable=True)
        if blocker == BLOCK_CONFLICT:
            return DomainError(code=_CONFLICT, name="MERGE_CONFLICT", retryable=False)
        if blocker == BLOCK_IN_PROGRESS:
            return DomainError(code=_IN_PROGRESS, name="GIT_OPERATION_IN_PROGRESS", retryable=False)
    if blockers:
        return DomainError(code=_MODE, name="MERGE_MODE_UNAVAILABLE", retryable=False, extra=blockers[:1])
    return None


@final
class GitMergeService:
    def __init__(
        self,
        state: GitStateService,
        operations: GitOperationService,
        writer: GitWriter,
        *,
        now: Callable[[], datetime],
        changed: Callable[[str], None],
    ) -> None:
        self._state: GitStateService = state
        self._operations: GitOperationService = operations
        self._writer: GitWriter = writer
        self._now: Callable[[], datetime] = now
        self._changed: Callable[[str], None] = changed
        self._tokens: Tokens[tuple[str, str, str, str, str]] = Tokens(now)
        self._journal: GitOperationJournal | None = None
        self._records: dict[str, GitOperationRecord] = {}
        self._unsaved: set[str] = set()
        self._guard: threading.Lock = threading.Lock()
        self.follow_up: FollowUp | None = None
        self.settle_rewrite: FollowUp | None = None
        state.operations = self.of_project

    def use_journal(self, journal: GitOperationJournal) -> None:
        """Settle what a crash interrupted. Runs before the socket accepts anyone."""
        self._journal = journal
        for record in journal.unfinished():
            with self._guard:
                self._records[record.id] = record
            if record.status == "started" and record.kind == "merge":
                self._settle(record)
            elif record.status == "started" and record.kind == "cleanup":
                gone = record.worktree is None or not Path(record.worktree).exists()
                self.store(record.moved(self._now(), "finished" if gone else "failed", message=INTERRUPTED))
        self.retry()

    def of_project(self, project_id: str) -> tuple[GitOperationView, ...]:
        with self._guard:
            records = tuple(self._records.values())
        return tuple(view_of(item) for item in records if item.project_id == project_id and item.open)

    def operation(self, params: GitOperationParams) -> GitOperationResult:
        with self._guard:
            found = self._records.get(params.operation_id)
        if found is None and self._journal is not None:
            found = self._journal.find(params.operation_id)
        if found is None:
            raise DomainError(code=_NOT_FOUND, name="GIT_OPERATION_NOT_FOUND", retryable=False)
        return GitOperationResult(operation=view_of(found))

    def preview(self, params: MergePreviewParams) -> MergePreviewResult:
        project_id = str(params.project_id)
        project = self._state.project(project_id)
        root = Path(project.root)
        refs = self._state.reader.refs(root)
        rows = self._state.reader.worktrees(root)
        source_row = _row_on(rows, params.source)
        session = None if source_row is None else self._state.session_at(project_id, source_row.path)
        target = params.target or (session.base_branch if session is not None else self._state.base_name(project_id))
        if target is None:
            raise DomainError(code=_BRANCH_UNKNOWN, name="GIT_BRANCH_UNKNOWN", retryable=False)
        source_oid = _oid_of(refs, params.source)
        self._require_recorded_source(project_id, params.source, source_oid)
        target_oid = _oid_of(refs, target)
        probe = self._state.probe(root, target_oid, source_oid)
        reader = self._state.reader
        already = reader.is_ancestor(root, source_oid, target_oid)
        forward = reader.is_ancestor(root, target_oid, source_oid)
        source_row = _row_on(rows, params.source)
        target_row = _row_on(rows, target)
        source_dirty = False
        if source_row is not None:
            source_status = reader.status(Path(source_row.path))
            source_dirty = bool(source_status.staged + source_status.unstaged + source_status.untracked)
        return MergePreviewResult(
            source=params.source,
            target=target,
            source_oid=source_oid,
            target_oid=target_oid,
            probe=probe,
            modes=merge_modes(clean=probe.status != "conflict", already_merged=already, can_fast_forward=forward),
            source_dirty=source_dirty,
            target_worktree=None if target_row is None else target_row.path,
            blockers=self._blockers(
                project_id,
                same=params.source == target,
                already=already,
                conflict=probe.status == "conflict",
                source_row=source_row,
                target_row=target_row,
            ),
            confirm_token=self._tokens.issue((project_id, params.source, target, source_oid, target_oid)),
        )

    def merge(self, params: MergeWorktreeParams) -> GitOperationResult:
        project_id = str(params.project_id)
        source_oid = require_oid(params.source_oid)
        target_oid = require_oid(params.target_oid)
        root = Path(self._state.project(project_id).root)
        with self._operations.lock_of(str(root)):
            self._tokens.spend((project_id, params.source, params.target, source_oid, target_oid), params.confirm_token)
            reader = self._state.reader
            refs = reader.refs(root)
            if _oid_of(refs, params.source) != source_oid or _oid_of(refs, params.target) != target_oid:
                raise DomainError(code=_STALE, name="MERGE_PREVIEW_STALE", retryable=True)
            self._require_recorded_source(project_id, params.source, source_oid)
            rows = reader.worktrees(root)
            source_row = _row_on(rows, params.source)
            target_row = _row_on(rows, params.target)
            if params.source.startswith("refs/aconsole/sessions/"):
                session_id = params.source.split("/")[3]
                session = self._state.session(project_id, session_id)
                if params.target != session.branch:
                    raise DomainError(code=3021, name="GIT_WORKTREE_UNKNOWN", retryable=False)
                self._state.require_active(project_id, session.worktree)
            for row in (source_row, target_row):
                if row is not None:
                    self._state.require_active(project_id, row.path)
            probe = reader.merge_probe(root, target_oid, source_oid)
            already = reader.is_ancestor(root, source_oid, target_oid)
            refusal = _refusal(
                self._blockers(
                    project_id,
                    same=params.source == params.target,
                    already=already,
                    conflict=not probe.clean,
                    source_row=source_row,
                    target_row=target_row,
                )
            )
            if refusal is not None:
                raise refusal
            forward = reader.is_ancestor(root, target_oid, source_oid)
            if params.mode not in merge_modes(clean=probe.clean, already_merged=already, can_fast_forward=forward):
                raise DomainError(code=_MODE, name="MERGE_MODE_UNAVAILABLE", retryable=False, extra=(params.mode,))
            record = self._begin(params, source_row, target_row, source_oid, target_oid, probe.tree)
            done = self._run(root, record, target_row, probe.tree)
            record = self._ended(root, record, done)
        self._changed(project_id)
        return GitOperationResult(operation=view_of(record))

    def _require_recorded_source(self, project_id: str, source: str, oid: str) -> None:
        """A mutable session ref must still identify its durable snapshot, including on generic Git RPCs."""
        if not source.startswith("refs/aconsole/sessions/"):
            return
        session_id = source.split("/")[3]
        _ = self._state.session(project_id, session_id)
        recorded = next((row for row in self._state.snapshots(session_id) if row.ref == source), None)
        if recorded is None or recorded.status != "finished" or recorded.oid != oid:
            raise DomainError(code=_STALE, name="MERGE_PREVIEW_STALE", retryable=True)

    def landed_from(self, project_id: str, source: str, oid: str) -> GitOperationRecord | None:
        with self._guard:
            records = tuple(self._records.values())
        for item in records:
            if (item.project_id, item.source, item.source_oid) == (project_id, source, oid) and item.result_oid:
                return item
        return None if self._journal is None else self._journal.landed_from(project_id, source, oid)

    def open_at(self, project_id: str, worktree: str) -> GitOperationRecord | None:
        with self._guard:
            records = tuple(self._records.values())
        for item in records:
            if item.project_id == project_id and item.worktree == worktree and item.status in {"started", "conflicted"}:
                return item
        return None

    def waiting(self) -> bool:
        """Something failed and needs another try, which no Git event would bring."""
        with self._guard:
            return bool(self._unsaved) or any(item.follow_up_pending for item in self._records.values())

    def retry(self) -> None:
        """Follow-ups a failure left behind, and journal rows that did not get written. Off the loop."""
        with self._guard:
            waiting = tuple(
                item for item in self._records.values() if item.follow_up_pending or item.status == "merged"
            )
            rewrites = tuple(item for item in self._records.values() if item.kind in _REWRITES and item.open)
            unsaved = tuple(self._records[key] for key in self._unsaved if key in self._records)
        for record in unsaved:
            self.store(record)
        for record in waiting:
            _ = self._follow(record)
        if self.settle_rewrite is not None:
            for record in rewrites:
                self.settle_rewrite(record)

    def _blockers(  # noqa: C901, PLR0913 -- one judgement, each word its own fact
        self,
        project_id: str,
        *,
        same: bool,
        already: bool,
        conflict: bool,
        source_row: WorktreeRow | None,
        target_row: WorktreeRow | None,
    ) -> tuple[str, ...]:
        found: list[str] = []
        if same:
            found.append(BLOCK_SAME)
        if already:
            found.append(BLOCK_MERGED)
        if conflict:
            found.append(BLOCK_CONFLICT)
        if source_row is not None and self._busy(project_id, source_row):
            found.append(BLOCK_SOURCE_BUSY)
        if source_row is not None and self._state.session_at(project_id, source_row.path) is not None:
            source_status = self._state.reader.status(Path(source_row.path))
            if source_status.staged + source_status.unstaged + source_status.untracked + source_status.conflicted:
                found.append(BLOCK_SOURCE_DIRTY)
        if target_row is not None:
            if self._busy(project_id, target_row):
                found.append(BLOCK_TARGET_BUSY)
            status = self._state.reader.status(Path(target_row.path))
            if status.in_progress is not None:
                found.append(BLOCK_IN_PROGRESS)
            if status.staged + status.unstaged + status.conflicted:
                found.append(BLOCK_TARGET_DIRTY)
        return tuple(found)

    def _busy(self, project_id: str, row: WorktreeRow) -> bool:
        return any(
            blocks_writes(TabStatus(tab.status.value)) for tab in self._state.tabs_at(project_id, row.path)
        )

    def _begin(  # noqa: PLR0913, PLR0917 -- what the journal must know before anything moves
        self,
        params: MergeWorktreeParams,
        source_row: WorktreeRow | None,
        target_row: WorktreeRow | None,
        source_oid: str,
        target_oid: str,
        tree: str | None,
    ) -> GitOperationRecord:
        project_id = str(params.project_id)
        tab = None if source_row is None else self._state.tab_at(project_id, source_row.path)
        session = None if source_row is None else self._state.session_at(project_id, source_row.path)
        if session is None and params.source.startswith("refs/aconsole/sessions/"):
            session = self._state.session(project_id, params.source.split("/")[3])
        at = self._now()
        record = GitOperationRecord(
            id=token_hex(_ID_BYTES),
            project_id=project_id,
            kind="merge",
            work_session_id=None if session is None else session.id,
            status="started",
            mode=params.mode,
            source=params.source,
            target=params.target,
            source_oid=source_oid,
            target_oid=target_oid,
            result_oid=None,
            expected_tree=tree,
            worktree=None if target_row is None else target_row.path,
            tab_id=None if tab is None else tab.tab_id,
            plan_node=None if tab is None else tab.plan_node,
            message=self._message(params, target_row, source_row),
            follow_up_pending=False,
            created_at=at,
            updated_at=at,
        )
        self.begin(record)
        return record

    def _message(self, params: MergeWorktreeParams, target: WorktreeRow | None, source: WorktreeRow | None) -> str:
        """A squash or merge commit is what stays on the base, so it says whose work this was."""
        text = params.message or _default_message(params.mode, params.source, params.target)
        if source is None or params.mode == "fast_forward":
            return text
        repo = Path(self._state.project(str(params.project_id)).root if target is None else target.path)
        trailers = self._state.merge_trailers_at(str(params.project_id), source.path)
        return self._writer.with_trailers(repo, text, trailers)

    def begin(self, record: GitOperationRecord) -> None:
        """The row is on disk before Git is touched, or Git is not touched."""
        if self._journal is None or not self._journal.save(record):
            raise DomainError(code=_NO_JOURNAL, name="DATABASE_TRANSACTION_FAILED", retryable=True)
        with self._guard:
            self._records[record.id] = record

    def _run(self, root: Path, record: GitOperationRecord, row: WorktreeRow | None, tree: str | None) -> GitDone:
        """Through the worktree that has the branch out, or straight on the ref when none does."""
        mode = record.mode or "merge"
        message = record.message or ""
        if row is not None:
            with self._operations.lock_of(row.path):
                return self._writer.merge(Path(row.path), record.source_oid, mode, message)
        ref = f"{_HEADS}{record.target}"
        if mode == "fast_forward":
            done = self._writer.move_ref(root, ref, record.source_oid, record.target_oid)
            return GitDone(ok=done.ok, said=done.said, oid=record.source_oid if done.ok else None)
        parents = (record.target_oid, record.source_oid) if mode == "merge" else (record.target_oid,)
        checked = self._writer.merge_hooks(root, tree or "", message, squash=mode == "squash")
        if not checked.ok:
            return checked
        made = self._writer.commit_tree(root, tree or "", parents, checked.text or message)
        if not made.ok or made.oid is None:
            return made
        moved = self._writer.move_ref(root, ref, made.oid, record.target_oid)
        return GitDone(ok=moved.ok, said=moved.said, oid=made.oid if moved.ok else None)

    def _ended(self, root: Path, record: GitOperationRecord, done: GitDone) -> GitOperationRecord:
        at = self._now()
        if not done.ok:
            failed = record.moved(at, "failed", message=done.said or record.message)
            self.store(failed)
            return failed
        result = done.oid or _oid_of(self._state.reader.refs(root), record.target)
        merged = record.moved(at, "merged", result_oid=result, follow_up_pending=True)
        self.store(merged)
        return self._follow(merged)

    def _follow(self, record: GitOperationRecord) -> GitOperationRecord:
        follow = self.follow_up
        if follow is not None:
            try:
                follow(record)
            except (DomainError, GitCommandError, StoreWriteError, OSError, TimeoutError) as exc:
                _LOG.warning("after merge %s: %s", record.id, exc)
                return record
        finished = record.moved(self._now(), "finished", follow_up_pending=False)
        self.store(finished)
        return finished

    def _settle(self, record: GitOperationRecord) -> None:
        """Decide from the branch, not from the journal, whether an interrupted merge landed."""
        try:
            root = Path(self._state.project(record.project_id).root)
            reader = self._state.reader
            head = _oid_of(reader.refs(root), record.target)
        except (DomainError, GitCommandError) as exc:
            self.store(record.moved(self._now(), "failed", message=f"{INTERRUPTED}: {exc}"))
            return
        if head == record.target_oid:
            self.store(record.moved(self._now(), "failed", message=INTERRUPTED))
            return
        rows = reader.log(root, (head,), 0, 1)
        parents = rows[0].parents if rows else ()
        if landed(record, head=head, parents=parents, tree=self._writer.tree_of(root, head)):
            self.store(record.moved(self._now(), "merged", result_oid=head, follow_up_pending=True))
            return
        self.store(record.moved(self._now(), "failed", message=TARGET_MOVED))

    def store(self, record: GitOperationRecord) -> None:
        with self._guard:
            self._records[record.id] = record
        saved = self._journal is None or self._journal.save(record)
        with self._guard:
            if saved:
                self._unsaved.discard(record.id)
            else:
                self._unsaved.add(record.id)
            if saved and not record.open and self._journal is not None:
                _ = self._records.pop(record.id, None)


def _default_message(mode: MergeMode, source: str, target: str) -> str:
    if mode == "squash":
        return f"Squash {source} into {target}"
    return f"Merge {source} into {target}"
