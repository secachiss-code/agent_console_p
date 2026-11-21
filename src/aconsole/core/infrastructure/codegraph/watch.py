"""Watch source files for a graph that has already been requested."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import final, override

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

_LOG = logging.getLogger(__name__)
_WRITES = frozenset({"created", "modified", "moved", "deleted"})
_SKIP = frozenset({"__pycache__", "node_modules", "venv", "site-packages", "build", "dist", "target", "bin", "obj"})
_SUFFIXES = frozenset({".py", ".pyi", ".rs", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".cs", ".csproj", ".sln"})
_CONFIG = frozenset(
    {
        "Cargo.toml",
        "Cargo.lock",
        "package.json",
        "tsconfig.json",
        "jsconfig.json",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "Directory.Build.props",
        "Directory.Build.targets",
    }
)


def _relevant(root: Path, path: Path, *, directory: bool) -> bool:
    if not path.is_relative_to(root):
        return False
    parts = path.relative_to(root).parts
    folders = parts if directory else parts[:-1]
    if any(part.startswith(".") or part in _SKIP for part in folders):
        return False
    return directory or path.suffix in _SUFFIXES or path.name in _CONFIG


class _Handler(FileSystemEventHandler):
    def __init__(self, root: Path, dirty: Callable[[Path], None]) -> None:
        self.root: Path = root
        self.dirty: Callable[[Path], None] = dirty

    @override
    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type not in _WRITES or (event.is_directory and event.event_type == "modified"):
            return
        for name in (event.src_path, event.dest_path):
            if isinstance(name, str) and name and _relevant(self.root, Path(name), directory=event.is_directory):
                self.dirty(self.root)
                break


@final
class SourceWatch:
    def __init__(self, dirty: Callable[[Path], None]) -> None:
        self._dirty = dirty
        self._lock = threading.Lock()
        self._observer: BaseObserver | None = None
        self._roots: set[Path] = set()
        self._closed = False

    def watch(self, root: Path) -> None:
        with self._lock:
            if self._closed or root in self._roots:
                return
            try:
                if self._observer is None:
                    observer = Observer()
                    observer.start()
                    self._observer = observer
                _ = self._observer.schedule(_Handler(root, self._dirty), str(root), recursive=True)
            except OSError as exc:
                _LOG.warning("code graph cannot watch %s: %s", root, exc)
            self._roots.add(root)

    def stop(self) -> None:
        with self._lock:
            self._closed = True
            observer = self._observer
        if observer is not None:
            observer.stop()
            observer.join()
