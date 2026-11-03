"""`git/*` on the wire, and telling the UI when a project's Git moved.

An agent's edits are not announced by anything, so while a client looks at a project this checks it now and then,
and at once after a turn ends. Single tool calls never trigger a look: a tab's status changes at most once a turn.
"""

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final, final

import msgspec

from aconsole.contracts.app import EmptyResult
from aconsole.contracts.common import ProjectId
from aconsole.contracts.events import CheckpointCreated, GitStateUpdated, TabUpdated, UiEvent
from aconsole.contracts.git import (
    CherryPickParams,
    CleanupWorktreeParams,
    GitCommitParams,
    GitDiffFileParams,
    GitDiffParams,
    GitGraphParams,
    GitOperationParams,
    GitPathsParams,
    GitStateParams,
    GitStateResult,
    GitWatchParams,
    MergePreviewParams,
    MergeWorktreeParams,
    RebaseParams,
    RestoreFileParams,
    RestoreFilePreviewParams,
    SendDiffParams,
    SendDiffPreviewParams,
    WorktreeOperationParams,
)
from aconsole.core.application.ports.git_repo import GitCommandError, GitWatcher
from aconsole.core.application.services.git_cleanup import GitCleanupService
from aconsole.core.application.services.git_merge import GitMergeService
from aconsole.core.application.services.git_ops import GitOperationService
from aconsole.core.application.services.git_rewrite import GitRewriteService
from aconsole.core.application.services.git_state import GitStateService

GIT_METHODS: Final = frozenset(
    {
        "git/state",
        "git/watch",
        "git/graph",
        "git/diffSummary",
        "git/diffFile",
        "git/stage",
        "git/unstage",
        "git/commit",
        "git/restoreFilePreview",
        "git/restoreFile",
        "git/mergePreview",
        "git/mergeWorktree",
        "git/operation",
        "git/cleanupWorktree",
        "git/factsToReview",
        "git/rebase",
        "git/cherryPick",
        "git/continue",
        "git/abort",
        "git/sendDiffPreview",
        "git/sendDiff",
    }
)
_LOG: Final = logging.getLogger(__name__)
_READS: Final = frozenset({"git/state", "git/watch", "git/graph", "git/diffSummary", "git/diffFile"})
_MERGING: Final = frozenset({"git/mergePreview", "git/mergeWorktree", "git/operation"})
_CLEANING: Final = frozenset({"git/cleanupWorktree", "git/factsToReview"})
_REWRITING: Final = frozenset(
    {"git/rebase", "git/cherryPick", "git/continue", "git/abort", "git/sendDiffPreview", "git/sendDiff"}
)
_WATCH_S: Final = 30.0
_QUIET_S: Final = 0.3
_RETRY_S: Final = 5.0


@final
class GitService:
    def __init__(
        self,
        state: GitStateService,
        *,
        emit: Callable[[UiEvent], None],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.state: GitStateService = state
        self.operations: GitOperationService | None = None
        self.merges: GitMergeService | None = None
        self.cleanup: GitCleanupService | None = None
        self.rewrite: GitRewriteService | None = None
        self._emit: Callable[[UiEvent], None] = emit
        self._monotonic: Callable[[], float] = monotonic
        self._lock: threading.Lock = threading.Lock()
        self._asked: dict[str, float] = {}
        self._revisions: dict[str, str] = {}
        self._nudged: set[str] = set()
        self._watching: set[str] = set()
        self._last_nudge: float = 0.0
        self.watcher: GitWatcher | None = None
        self.wake: Callable[[], None] = lambda: None

    def dispatch(self, method: str, raw: bytes) -> msgspec.Struct:
        if method in _REWRITING and self.rewrite is not None:
            return self._redo(self.rewrite, method, raw)
        if method in _CLEANING and self.cleanup is not None:
            return self._clean(self.cleanup, method, raw)
        if method in _MERGING and self.merges is not None:
            return self._merge(self.merges, method, raw)
        if method not in _READS and self.operations is not None:
            return self._write(self.operations, method, raw)
        return self._read(method, raw)

    def _read(self, method: str, raw: bytes) -> msgspec.Struct:
        if method == "git/state":
            return self._state(str(msgspec.json.decode(raw, type=GitStateParams).project_id))
        if method == "git/watch":
            self.keep_watching(str(msgspec.json.decode(raw, type=GitWatchParams).project_id))
            return EmptyResult()
        if method == "git/graph":
            return self.state.graph(msgspec.json.decode(raw, type=GitGraphParams))
        if method == "git/diffSummary":
            summary = msgspec.json.decode(raw, type=GitDiffParams)
            return self.state.diff_summary(str(summary.project_id), summary.source)
        detail = msgspec.json.decode(raw, type=GitDiffFileParams)
        return self.state.diff_file(str(detail.project_id), detail.source, detail.path, detail.max_lines)

    def _redo(self, rewrite: GitRewriteService, method: str, raw: bytes) -> msgspec.Struct:
        if method == "git/rebase":
            return rewrite.rebase(msgspec.json.decode(raw, type=RebaseParams))
        if method == "git/cherryPick":
            return rewrite.cherry_pick(msgspec.json.decode(raw, type=CherryPickParams))
        if method == "git/continue":
            return rewrite.resume(msgspec.json.decode(raw, type=WorktreeOperationParams))
        if method == "git/abort":
            return rewrite.abort(msgspec.json.decode(raw, type=WorktreeOperationParams))
        if method == "git/sendDiffPreview":
            return rewrite.send_preview(msgspec.json.decode(raw, type=SendDiffPreviewParams))
        return rewrite.send(msgspec.json.decode(raw, type=SendDiffParams))

    def _clean(self, cleanup: GitCleanupService, method: str, raw: bytes) -> msgspec.Struct:
        if method == "git/cleanupWorktree":
            return cleanup.cleanup(msgspec.json.decode(raw, type=CleanupWorktreeParams))
        return cleanup.facts_to_review(str(msgspec.json.decode(raw, type=GitStateParams).project_id))

    def _merge(self, merges: GitMergeService, method: str, raw: bytes) -> msgspec.Struct:
        if method == "git/mergePreview":
            return merges.preview(msgspec.json.decode(raw, type=MergePreviewParams))
        if method == "git/mergeWorktree":
            return merges.merge(msgspec.json.decode(raw, type=MergeWorktreeParams))
        return merges.operation(msgspec.json.decode(raw, type=GitOperationParams))

    def _write(self, operations: GitOperationService, method: str, raw: bytes) -> msgspec.Struct:
        if method == "git/stage":
            return operations.stage(msgspec.json.decode(raw, type=GitPathsParams))
        if method == "git/unstage":
            return operations.unstage(msgspec.json.decode(raw, type=GitPathsParams))
        if method == "git/commit":
            return operations.commit(msgspec.json.decode(raw, type=GitCommitParams))
        if method == "git/restoreFilePreview":
            return operations.restore_preview(msgspec.json.decode(raw, type=RestoreFilePreviewParams))
        return operations.restore_file(msgspec.json.decode(raw, type=RestoreFileParams))

    def on_event(self, event: UiEvent) -> None:
        """A turn ended or a checkpoint was taken: the next check should not wait."""
        if isinstance(event, TabUpdated) and "status" in event.changed:
            self._nudge(str(event.tab.project_id))
        elif isinstance(event, CheckpointCreated):
            with self._lock:
                self._nudged.update(self._asked)
                self._last_nudge = self._monotonic()
            self.wake()

    def heard(self, project_id: str) -> None:
        """Git changed this repository. From the watcher's thread."""
        self._nudge(project_id)

    def check(self) -> tuple[str, ...]:
        """Look at what changed in projects a client is showing. Off the loop: it runs git."""
        if self.merges is not None:
            self.merges.retry()
        now = self._monotonic()
        with self._lock:
            self._asked = {key: at for key, at in self._asked.items() if now - at < _WATCH_S}
            changed = tuple(key for key in self._nudged if key in self._asked)
            self._nudged.clear()
        return tuple(project for project in changed if self.announce(project))

    def settled_in(self) -> float | None:
        """Seconds until a burst of writes has gone quiet. None when nothing is waiting for a look."""
        with self._lock:
            if not self._nudged:
                return None
            return max(0.0, self._last_nudge + _QUIET_S - self._monotonic())

    def retry_in(self) -> float | None:
        """A follow-up that failed is the one thing tried again on a timer, and only while it is failing."""
        return _RETRY_S if self.merges is not None and self.merges.waiting() else None

    def announce(self, project_id: str) -> bool:
        """Say so when this project's revision is not the one a client last saw."""
        state = self.state.state(project_id)
        with self._lock:
            if self._revisions.get(project_id) == state.revision:
                return False
            self._revisions[project_id] = state.revision
        self._emit(GitStateUpdated(project_id=ProjectId(project_id), revision=state.revision))
        return True

    def _nudge(self, project_id: str) -> None:
        with self._lock:
            self._nudged.add(project_id)
            self._last_nudge = self._monotonic()
        self.wake()

    def keep_watching(self, project_id: str) -> None:
        """A client still shows this project. Costs one `rev-parse` once per project, then nothing."""
        with self._lock:
            self._asked[project_id] = self._monotonic()
            known = project_id in self._watching
            self._watching.add(project_id)
        if known or self.watcher is None:
            return
        root = Path(self.state.project(project_id).root)
        try:
            self.watcher.watch(project_id, self.state.reader.common_dir(root))
        except GitCommandError as exc:
            _LOG.info("%s cannot be watched yet: %s", root, exc.detail)
            with self._lock:
                self._watching.discard(project_id)

    def _state(self, project_id: str) -> GitStateResult:
        state = self.state.state(project_id)
        self.keep_watching(project_id)
        with self._lock:
            self._revisions[project_id] = state.revision
        return state
