"""Hear Git change a repository, without asking it.

The watches cover what Git itself writes: HEAD, the index, refs, and the same for each linked worktree. Objects, logs
and lock files are left out, and so are reads, so the core's own `git status` never wakes it. An edit to a file that
nobody has staged touches none of these. Those appear when a turn ends, or when the user refreshes.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Final, final, override

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

_LOG: Final = logging.getLogger(__name__)
_WRITES: Final = frozenset({"created", "modified", "moved", "deleted"})
_STATE_FILES: Final = frozenset(
    {
        "HEAD",
        "index",
        "packed-refs",
        "ORIG_HEAD",
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "REBASE_HEAD",
        "BISECT_LOG",
    }
)
_REFS: Final = "refs"
_WORKTREES: Final = "worktrees"
_SEQUENCES: Final = frozenset({"rebase-merge", "rebase-apply"})
_LOCK: Final = ".lock"


def means_git_changed(common_dir: Path, path: str) -> bool:
    """Whether a write at `path` can change what the Git page shows."""
    name = Path(path)
    if name.name.endswith(_LOCK):
        return False
    try:
        parts = name.relative_to(common_dir).parts
    except ValueError:
        return False
    if not parts:
        return False
    if parts[0] == _REFS:
        return True
    if parts[0] == _WORKTREES:
        # A worktree came or went, or one of its own state files moved.
        return len(parts) <= 2 or _names_state(parts[2:])  # noqa: PLR2004 -- worktrees/<name>
    return _names_state(parts)


def _names_state(parts: tuple[str, ...]) -> bool:
    return parts[0] in _STATE_FILES or parts[0] in _SEQUENCES


class _Handler(FileSystemEventHandler):
    def __init__(self, common_dir: Path, heard: Callable[[], None], appeared: Callable[[], None]) -> None:
        self._common_dir: Path = common_dir
        self._heard: Callable[[], None] = heard
        self._appeared: Callable[[], None] = appeared

    @override
    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type not in _WRITES:
            return
        # Git writes `index.lock` and renames it. What counts is where it ended up.
        paths = (str(event.src_path), str(event.dest_path)) if event.dest_path else (str(event.src_path),)
        if any(Path(path) == self._common_dir / _WORKTREES for path in paths):
            self._appeared()
        if any(means_git_changed(self._common_dir, path) for path in paths):
            self._heard()


@final
class GitWatch:
    """One observer thread for all projects. It starts with the first project, so a core with none costs nothing."""

    def __init__(self, heard: Callable[[str], None]) -> None:
        self._heard: Callable[[str], None] = heard
        self._lock: threading.Lock = threading.Lock()
        self._observer: BaseObserver | None = None
        self._watched: dict[str, Path] = {}

    def watch(self, project_id: str, common_dir: Path) -> None:
        with self._lock:
            if self._watched.get(project_id) == common_dir:
                return
            self._watched[project_id] = common_dir
            if self._observer is None:
                self._observer = Observer()
                self._observer.daemon = True
                self._observer.start()
            observer = self._observer

            def heard() -> None:
                self._heard(project_id)

            def appeared() -> None:
                self._schedule(observer, handler, common_dir / _WORKTREES, recursive=True)

            handler = _Handler(common_dir, heard, appeared)
            # Only these, so writing objects and logs costs no watch and wakes nothing.
            self._schedule(observer, handler, common_dir, recursive=False)
            self._schedule(observer, handler, common_dir / _REFS, recursive=True)
            self._schedule(observer, handler, common_dir / _WORKTREES, recursive=True)

    def _schedule(self, observer: BaseObserver, handler: _Handler, path: Path, *, recursive: bool) -> None:
        if not path.is_dir():
            return
        try:
            _ = observer.schedule(handler, str(path), recursive=recursive)
        except OSError as exc:
            # No more inotify watches on this machine. The page still works, by its Refresh button and turn ends.
            _LOG.warning("cannot watch %s: %s", path, exc)

    def stop(self) -> None:
        with self._lock:
            observer = self._observer
            self._observer = None
            self._watched.clear()
        if observer is not None:
            observer.stop()
            observer.join(timeout=2)
