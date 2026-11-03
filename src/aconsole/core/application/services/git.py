"""`git/*` on the wire, and telling the UI when a project's Git moved.

An agent's edits are not announced by anything, so while a client looks at a project this checks it now and then,
and at once after a turn ends. Single tool calls never trigger a look: a tab's status changes at most once a turn.
"""

import threading
import time
from collections.abc import Callable
from typing import Final, final

import msgspec

from aconsole.contracts.common import ProjectId
from aconsole.contracts.events import CheckpointCreated, GitStateUpdated, TabUpdated, UiEvent
from aconsole.contracts.git import (
    GitCommitParams,
    GitDiffFileParams,
    GitDiffParams,
    GitGraphParams,
    GitPathsParams,
    GitStateParams,
    GitStateResult,
    RestoreFileParams,
    RestoreFilePreviewParams,
)
from aconsole.core.application.services.git_ops import GitOperationService
from aconsole.core.application.services.git_state import GitStateService

GIT_METHODS: Final = frozenset(
    {
        "git/state",
        "git/graph",
        "git/diffSummary",
        "git/diffFile",
        "git/stage",
        "git/unstage",
        "git/commit",
        "git/restoreFilePreview",
        "git/restoreFile",
    }
)
_READS: Final = frozenset({"git/state", "git/graph", "git/diffSummary", "git/diffFile"})
_WATCH_S: Final = 30.0


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
        self._emit: Callable[[UiEvent], None] = emit
        self._monotonic: Callable[[], float] = monotonic
        self._lock: threading.Lock = threading.Lock()
        self._asked: dict[str, float] = {}
        self._revisions: dict[str, str] = {}
        self._nudged: set[str] = set()

    def dispatch(self, method: str, raw: bytes) -> msgspec.Struct:
        if method not in _READS and self.operations is not None:
            return self._write(self.operations, method, raw)
        if method == "git/state":
            return self._state(str(msgspec.json.decode(raw, type=GitStateParams).project_id))
        if method == "git/graph":
            return self.state.graph(msgspec.json.decode(raw, type=GitGraphParams))
        if method == "git/diffSummary":
            summary = msgspec.json.decode(raw, type=GitDiffParams)
            return self.state.diff_summary(str(summary.project_id), summary.source)
        detail = msgspec.json.decode(raw, type=GitDiffFileParams)
        return self.state.diff_file(str(detail.project_id), detail.source, detail.path, detail.max_lines)

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

    def check(self) -> tuple[str, ...]:
        """One pass over the projects a client looked at lately. Off the loop: it runs git."""
        now = self._monotonic()
        with self._lock:
            self._asked = {key: at for key, at in self._asked.items() if now - at < _WATCH_S}
            watched = tuple(self._asked)
            self._nudged.clear()
        return tuple(project for project in watched if self.announce(project))

    def nudged(self) -> bool:
        with self._lock:
            return bool(self._nudged)

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
            if project_id in self._asked:
                self._nudged.add(project_id)

    def _state(self, project_id: str) -> GitStateResult:
        state = self.state.state(project_id)
        with self._lock:
            self._asked[project_id] = self._monotonic()
            self._revisions[project_id] = state.revision
        return state
