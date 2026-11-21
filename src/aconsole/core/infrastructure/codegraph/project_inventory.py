"""File and symbol inventory of the active worktree, including non-code paths."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import stat
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import final

from aconsole.core.application.ports.git_repo import GitCommandError
from aconsole.core.infrastructure.git.runner import GitRunner

_MAX_FILE_BYTES = 512 * 1024
_MAX_ROOTS = 4
_MAX_FILES = 50_000
_MAX_CONTENT_BYTES = 32 * 1024 * 1024
_SKIP_DIRS = frozenset({".git", ".aconsole", ".aws", ".codex", ".agents", "node_modules", "__pycache__"})
_PRIVATE_NAMES = frozenset({".env", ".netrc", ".npmrc", "id_rsa", "id_ed25519"})
_UNSAFE_CHARS = re.compile(r"[\x00-\x1f\ud800-\udfff]")
type FileStamp = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class Symbol:
    name: str
    start_line: int
    end_line: int
    occurrence: int


@dataclass(frozen=True, slots=True)
class InventoryFile:
    path: str
    size: int
    stamp: FileStamp
    sha256: str | None = None
    text: str | None = None
    symbols: tuple[Symbol, ...] = ()
    metadata_only: bool = False


@dataclass(frozen=True, slots=True)
class WorktreeInventory:
    revision: str
    files: dict[str, InventoryFile]
    submodules: tuple[str, ...]
    complete: bool


def permitted_path(path: str) -> bool:
    parts = path.split("/")
    return (
        all(part not in {"", ".", ".."} and part not in _SKIP_DIRS for part in parts)
        and _UNSAFE_CHARS.search(path) is None
        and parts[-1] not in _PRIVATE_NAMES
        and not parts[-1].startswith(".env.")
    )


def _stat_stamp(info: os.stat_result) -> FileStamp:
    return info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _open_relative(root: Path, path: str, stack: ExitStack) -> int:
    if not permitted_path(path):
        message = "path lies outside the indexed files"
        raise FileNotFoundError(message)
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = os.open(root, flags | os.O_DIRECTORY)
    _ = stack.callback(os.close, descriptor)
    parts = path.split("/")
    for part in parts[:-1]:
        descriptor = os.open(part, flags | os.O_DIRECTORY, dir_fd=descriptor)
        _ = stack.callback(os.close, descriptor)
    fd = os.open(parts[-1], flags | os.O_NONBLOCK, dir_fd=descriptor)
    _ = stack.callback(os.close, fd)
    return fd


def read_file(root: Path, path: str, cached: InventoryFile | None = None) -> InventoryFile | None:
    """Read only a regular file beneath the authorized worktree."""
    try:
        with ExitStack() as stack:
            fd = _open_relative(root, path, stack)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                return None
            stamp = _stat_stamp(info)
            if cached is not None and cached.stamp == stamp and not cached.metadata_only:
                return cached
            if info.st_size > _MAX_FILE_BYTES:
                return InventoryFile(path, info.st_size, stamp)
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(_MAX_FILE_BYTES + 1)
            if len(raw) > _MAX_FILE_BYTES or _stat_stamp(os.fstat(fd)) != stamp:
                return None
    except OSError:
        return None
    return _decoded_file(path, raw, stamp)


def _decoded_file(path: str, raw: bytes, stamp: FileStamp) -> InventoryFile:
    entry = InventoryFile(path, len(raw), stamp, hashlib.sha256(raw).hexdigest())
    if b"\0" in raw:
        return entry
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return entry
    symbols = python_symbols(content) if PurePosixPath(path).suffix in {".py", ".pyi"} else ()
    return replace(entry, text=content, symbols=symbols)


def python_symbols(source: str) -> tuple[Symbol, ...]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return ()
    occurrences: dict[str, int] = {}
    result: list[Symbol] = []

    def visit(node: ast.AST, prefix: str) -> None:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = f"{prefix}.{node.name}" if prefix else node.name
            count = occurrences.get(prefix, 0) + 1
            occurrences[prefix] = count
            first = min((item.lineno for item in node.decorator_list), default=node.lineno)
            result.append(Symbol(prefix, first, node.end_lineno or node.lineno, count))
        for child in ast.iter_child_nodes(node):
            visit(child, prefix)

    try:
        visit(tree, "")
    except RecursionError:
        return ()
    return tuple(result)


def _walk(root: Path) -> Iterator[str]:
    for directory, subdirs, names in os.walk(root, followlinks=False):
        subdirs[:] = sorted(name for name in subdirs if not name.startswith(".") and name not in _SKIP_DIRS)
        for name in sorted(names):
            yield (Path(directory) / name).relative_to(root).as_posix()


@final
class ProjectInventories:
    """Reuse unchanged files and cap retained worktree snapshots."""

    def __init__(self) -> None:
        self._git = GitRunner(timeout_s=5.0)
        self._lock = threading.RLock()
        self._roots: OrderedDict[Path, WorktreeInventory] = OrderedDict()

    def snapshot(self, root: Path) -> WorktreeInventory:
        root = root.resolve()
        with self._lock:
            previous = self._roots.get(root)
            inventory = self._snapshot(root, previous)
            self._roots[root] = inventory
            self._roots.move_to_end(root)
            if len(self._roots) > _MAX_ROOTS:
                _ = self._roots.popitem(last=False)
            return inventory

    def _paths(self, root: Path) -> tuple[Iterator[str], tuple[str, ...]]:
        try:
            listing = self._git.read(root, "ls-files", "-z", "--stage", "--cached", "--others", "--exclude-standard")
        except GitCommandError as exc:
            if "not a git repository" not in exc.detail:
                raise
            return _walk(root), ()
        names, submodules = _git_paths(listing.stdout)
        return iter(names), submodules

    def _snapshot(self, root: Path, previous: WorktreeInventory | None) -> WorktreeInventory:
        paths, submodules = self._paths(root)
        files: dict[str, InventoryFile] = {}
        content_bytes = 0
        complete = True
        for path in paths:
            if not permitted_path(path):
                continue
            if len(files) + len(submodules) >= _MAX_FILES:
                complete = False
                break
            cached = None if previous is None else previous.files.get(path)
            found = read_file(root, path, cached)
            if found is not None:
                if found.text is not None:
                    content_bytes += found.size
                    if content_bytes > _MAX_CONTENT_BYTES:
                        complete = False
                        found = replace(found, text=None, symbols=(), metadata_only=True)
                files[path] = found
        digest = hashlib.sha256()
        for name, item in sorted(files.items()):
            digest.update(repr((name, item.sha256 or item.stamp)).encode())
        digest.update(repr((submodules, complete)).encode())
        return WorktreeInventory(digest.hexdigest(), files, submodules, complete)


def _git_paths(raw: bytes) -> tuple[tuple[str, ...], tuple[str, ...]]:
    names: set[str] = set()
    submodules: set[str] = set()
    for row in raw.decode("utf-8", "surrogateescape").split("\0"):
        metadata, tab, name = row.partition("\t")
        if tab and re.fullmatch(r"[0-7]{6} [0-9a-f]{40,64} [0-3]", metadata):
            if metadata.startswith("160000 ") and permitted_path(name):
                submodules.add(name)
            else:
                names.add(name)
        else:
            names.add(row)
    return tuple(sorted(names - submodules)), tuple(sorted(submodules))
