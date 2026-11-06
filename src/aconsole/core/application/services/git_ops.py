"""What changes a project's Git. Every call names a worktree and what the user saw there.

Writes to one worktree run one at a time; what touches shared refs takes the repository's lock first. An agent's
worktree is left alone while the agent may be writing. Git itself may still refuse, through a hook or signing:
that is an answer for the user (`ok` false), not an error of this core.
"""

import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from secrets import compare_digest, token_hex
from typing import Final, final

from aconsole.contracts.git import (
    GitChangeResult,
    GitCommitParams,
    GitPathsParams,
    RestoreFileParams,
    RestoreFilePreviewParams,
    RestoreFilePreviewResult,
)
from aconsole.core.application.ports.git_repo import GitDone, GitReader, GitWriter, StatusRow, WorktreeRow
from aconsole.core.application.services.git_state import GitStateService, GitTab
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.git_state import require_fresh, require_idle, require_nothing_in_progress, require_oid
from aconsole.core.domain.tab_status import TabStatus

_TOKEN_BYTES: Final = 16
_TOKEN_TTL: Final = timedelta(minutes=10)
_BAD_TOKEN: Final = 3007

type Safety = Callable[[str, str], None]


def require_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    """Paths inside the worktree. Git checks too, but its answer would come after the lock and the journal."""
    if not paths:
        message = "no paths"
        raise ValueError(message)
    for path in paths:
        pure = PurePosixPath(path)
        if path == "" or pure.is_absolute() or ".." in pure.parts:
            message = "path must stay inside the worktree"
            raise ValueError(message)
    return paths


def bad_token() -> DomainError:
    return DomainError(code=_BAD_TOKEN, name="CONFIRM_TOKEN_INVALID", retryable=False)


class Tokens[Key]:
    """One-shot confirmations bound to exactly what the preview showed."""

    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now: Callable[[], datetime] = now
        self._items: dict[Key, tuple[str, datetime]] = {}
        self._lock: threading.Lock = threading.Lock()

    def issue(self, key: Key) -> str:
        token = token_hex(_TOKEN_BYTES)
        with self._lock:
            self._items[key] = (token, self._now() + _TOKEN_TTL)
        return token

    def spend(self, key: Key, token: str) -> None:
        with self._lock:
            found = self._items.pop(key, None)
        if found is None or not compare_digest(found[0], token) or self._now() >= found[1]:
            raise bad_token()


@final
class GitOperationService:
    def __init__(
        self,
        state: GitStateService,
        writer: GitWriter,
        *,
        now: Callable[[], datetime],
        tab_safety: Safety,
        changed: Callable[[str], None],
    ) -> None:
        self._state: GitStateService = state
        self._reader: GitReader = state.reader
        self._writer: GitWriter = writer
        self._now: Callable[[], datetime] = now
        self._tab_safety: Safety = tab_safety
        self._changed: Callable[[str], None] = changed
        self._guard: threading.Lock = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}
        self._restores: Tokens[tuple[str, str, str, str]] = Tokens(now)

    def lock_of(self, key: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(key, threading.RLock())

    def stage(self, params: GitPathsParams) -> GitChangeResult:
        paths = require_paths(params.paths)
        return self._change(
            params,
            lambda repo: self._writer.stage(repo, paths),
        )

    def unstage(self, params: GitPathsParams) -> GitChangeResult:
        paths = require_paths(params.paths)
        return self._change(
            params,
            lambda repo: self._writer.unstage(repo, paths, has_head=params.expect_head is not None),
        )

    def commit(self, params: GitCommitParams) -> GitChangeResult:
        trailers = self._state.trailers_at(str(params.project_id), params.worktree, params.actor_tab_id)

        def commit(repo: Path) -> GitDone:
            return self._writer.commit(repo, self._writer.with_trailers(repo, params.message, trailers))

        return self._change(params, commit)

    def restore_preview(self, params: RestoreFilePreviewParams) -> RestoreFilePreviewResult:
        path = require_paths((params.path,))[0]
        oid = require_oid(params.source_oid)
        _root, row = self._state.worktree_row(str(params.project_id), params.worktree)
        repo = Path(row.path)
        insertions, deletions = self._writer.file_counts(repo, oid, path)
        return RestoreFilePreviewResult(
            path=path,
            source_oid=oid,
            insertions=insertions,
            deletions=deletions,
            exists_in_source=self._writer.in_source(repo, oid, path),
            confirm_token=self._restores.issue((str(params.project_id), row.path, path, oid)),
        )

    def restore_file(self, params: RestoreFileParams) -> GitChangeResult:
        """Keep the worktree on a side ref, then put back one file. A failed restore loses nothing."""
        path = require_paths((params.path,))[0]
        oid = require_oid(params.source_oid)
        project_id = str(params.project_id)
        _root, row = self._state.worktree_row(project_id, params.worktree)
        with self.lock_of(row.path):
            _ = self.require_quiet(project_id, row)
            self._restores.spend((project_id, row.path, path, oid), params.confirm_token)
            label = f"before restoring {path}"
            tab = self._state.tab_at(project_id, row.path)
            repo = Path(row.path)
            if tab is not None:
                self._tab_safety(tab.tab_id, label)
            else:
                self._writer.safety_ref(repo, f"refs/aconsole/safety/{token_hex(_TOKEN_BYTES)}", label)
            done = self._writer.restore_file(repo, oid, path)
            return self._answer(project_id, repo, done)

    def tab_of(self, project_id: str, row: WorktreeRow) -> GitTab | None:
        return self._state.tab_at(project_id, row.path)

    def require_quiet(self, project_id: str, row: WorktreeRow) -> StatusRow:
        """No agent writing here, and no merge or rebase halfway."""
        self.require_no_agent(project_id, row)
        status = self._reader.status(Path(row.path))
        require_nothing_in_progress(status.in_progress)
        return status

    def require_no_agent(self, project_id: str, row: WorktreeRow) -> None:
        self._state.require_active(project_id, row.path)
        for tab in self._state.tabs_at(project_id, row.path):
            require_idle(TabStatus(tab.status.value))

    def _change(
        self,
        params: GitPathsParams | GitCommitParams,
        run: Callable[[Path], GitDone],
    ) -> GitChangeResult:
        project_id = str(params.project_id)
        _root, row = self._state.worktree_row(project_id, params.worktree)
        repo = Path(row.path)
        with self.lock_of(row.path):
            self.require_no_agent(project_id, row)
            status = self._reader.status(repo)
            require_fresh(
                expected_head=params.expect_head,
                head=self._writer.head(repo),
                expected_index=params.expect_index,
                index=status.index_digest,
            )
            return self._answer(project_id, repo, run(repo))

    def _answer(self, project_id: str, repo: Path, done: GitDone) -> GitChangeResult:
        self._changed(project_id)
        return GitChangeResult(ok=done.ok, head_oid=self._writer.head(repo), detail=done.said or None)
