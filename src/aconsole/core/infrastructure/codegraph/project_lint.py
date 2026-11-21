"""Run the bundled Ruff against a private, immutable set of source inputs."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import threading
import tomllib
from collections import OrderedDict
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Literal, final

import msgspec
from pydantic import BaseModel

from aconsole.core.infrastructure.codegraph.project_inventory import InventoryFile, WorktreeInventory, permitted_path

type LintStatus = Literal["clean", "findings", "no_targets", "unavailable", "timeout", "tool_error", "stale"]
_CONFIG_NAMES = frozenset({"pyproject.toml", "ruff.toml", ".ruff.toml", ".gitignore"})
_MAX_CACHE = 24
_MAX_TARGETS = 60
_MAX_OUTPUT = 2 * 1024 * 1024
_TIMEOUT = 15


class Position(msgspec.Struct):
    row: int
    column: int


class RawDiagnostic(msgspec.Struct):
    code: str | None
    filename: str
    location: Position
    end_location: Position
    message: str


class Diagnostic(BaseModel, frozen=True):
    path: str
    sha256: str | None
    code: str | None
    line: int
    column: int
    end_line: int
    end_column: int
    message: str
    symbol: str | None = None


class LintResult(BaseModel, frozen=True):
    revision: str
    state: LintStatus
    targeted: int
    checked: int = 0
    total: int = 0
    diagnostics: tuple[Diagnostic, ...] = ()
    cursor: str | None = None
    cached: bool = False
    node_id: str | None = None
    truncated: bool = False


def lint_targets(inventory: WorktreeInventory, paths: tuple[str, ...]) -> tuple[tuple[InventoryFile, ...], bool]:
    files = tuple(
        item for path in sorted(set(paths))
        if PurePosixPath(path).suffix in {".py", ".pyi"}
        and (item := inventory.files.get(path)) is not None and item.text is not None
    )
    return files[:_MAX_TARGETS], len(files) > _MAX_TARGETS


def _configurations(inventory: WorktreeInventory) -> dict[str, str]:
    found: dict[str, str] = {}
    pending = [name for name in inventory.files if PurePosixPath(name).name in _CONFIG_NAMES]
    while pending:
        path = pending.pop()
        if path in found:
            continue
        item = inventory.files[path]
        if item.text is None:
            message = "Ruff configuration exceeds the indexed text budget"
            raise ValueError(message)
        found[path] = item.text
        parent = _config_parent(path, item.text)
        if parent is not None:
            if parent not in inventory.files or not permitted_path(parent):
                message = "Ruff extends a configuration outside the indexed worktree"
                raise ValueError(message)
            pending.append(parent)
    return found


def _config_parent(path: str, text: str) -> str | None:
    manifest = PurePosixPath(path)
    if manifest.name == ".gitignore":
        return None
    settings = msgspec.convert(tomllib.loads(text), type=dict[str, object])
    if manifest.name == "pyproject.toml":
        tools = msgspec.convert(settings.get("tool", {}), type=dict[str, object])
        settings = msgspec.convert(tools.get("ruff", {}), type=dict[str, object])
    extend = settings.get("extend")
    if not isinstance(extend, str):
        return None
    # Keep relative includes inside the staged snapshot.
    if not permitted_path(extend):
        message = "Ruff configuration extends an unsafe path"
        raise ValueError(message)
    return str(manifest.parent / extend)


@final
class ProjectLinter:
    def __init__(self, binary: Path | None = None) -> None:
        self._binary = Path(sys.prefix) / "bin" / "ruff" if binary is None else binary
        self._cache: OrderedDict[str, LintResult] = OrderedDict()
        self._lock = threading.Lock()

    def run(self, root: Path, inventory: WorktreeInventory, paths: tuple[str, ...]) -> LintResult:
        files, truncated = lint_targets(inventory, paths)
        base = LintResult(revision=inventory.revision, state="no_targets", targeted=len(files), truncated=truncated)
        if not files:
            return base
        if not self._binary.is_absolute() or not self._binary.is_file():
            return base.model_copy(update={"state": "unavailable"})
        try:
            configs = _configurations(inventory)
            info = self._binary.stat()
            key = hashlib.sha256(repr((
                root, info.st_ino, info.st_size, info.st_mtime_ns, tuple(sorted(configs.items())),
                tuple((item.path, item.sha256) for item in files), truncated,
            )).encode()).hexdigest()
        except (ValueError, OSError, msgspec.ValidationError):
            return base.model_copy(update={"state": "tool_error"})
        with self._lock:
            if (previous := self._cache.get(key)) is not None:
                self._cache.move_to_end(key)
                return previous.model_copy(update={"revision": inventory.revision, "cached": True})
            result = self._run(base, files, configs)
            if result.state in {"clean", "findings"}:
                self._cache[key] = result
                if len(self._cache) > _MAX_CACHE:
                    _ = self._cache.popitem(last=False)
            return result

    def _run(
        self, base: LintResult, files: tuple[InventoryFile, ...], configs: dict[str, str]
    ) -> LintResult:
        try:
            with TemporaryDirectory(prefix="aconsole-ruff-") as temporary:
                root = Path(temporary)
                workspace = root / "sources"
                _stage(workspace, files, configs)
                diagnostics, checked = self._check(root, workspace, files)
        except subprocess.TimeoutExpired:
            return base.model_copy(update={"state": "timeout"})
        except (OSError, RuntimeError, msgspec.DecodeError):
            return base.model_copy(update={"state": "tool_error"})
        return base.model_copy(update={
            "state": "findings" if diagnostics else "clean", "checked": checked, "diagnostics": diagnostics,
            "total": len(diagnostics),
        })

    def _check(
        self, scratch: Path, workspace: Path, files: tuple[InventoryFile, ...]
    ) -> tuple[tuple[Diagnostic, ...], int]:
        env = {"HOME": str(scratch), "XDG_CONFIG_HOME": str(scratch), "LC_ALL": "C.UTF-8"}
        selected = tuple(item.path for item in files)
        common = (str(self._binary), "check", "--no-cache", "--no-fix", "--force-exclude")
        with (scratch / "ruff.json").open("w+b") as output:
            completed = subprocess.run(  # noqa: S603 -- only bundled Ruff and literal indexed paths
                (*common, "--output-format=json", "--", *selected), cwd=workspace, env=env, check=False,
                timeout=_TIMEOUT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
            )
            if completed.returncode not in {0, 1} or output.tell() > _MAX_OUTPUT:
                message = "Ruff failed or exceeded its output budget"
                raise RuntimeError(message)
            _ = output.seek(0)
            rows = msgspec.json.decode(output.read(), type=list[RawDiagnostic])
        checked = subprocess.run(  # noqa: S603 -- same scope, reports exclusions
            (*common, "--show-files", "--", *selected), cwd=workspace, env=env, check=False,
            timeout=_TIMEOUT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        if checked.returncode:
            message = "Ruff could not determine analyzed files"
            raise RuntimeError(message)
        return _diagnostics(workspace, rows, files), len(checked.stdout.splitlines())


def _stage(workspace: Path, files: tuple[InventoryFile, ...], configs: dict[str, str]) -> None:
    content = {**configs, **{item.path: item.text or "" for item in files}}
    for name, text in content.items():
        destination = workspace / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = destination.write_text(text, encoding="utf-8")


def _diagnostics(
    workspace: Path, rows: list[RawDiagnostic], files: tuple[InventoryFile, ...]
) -> tuple[Diagnostic, ...]:
    by_path = {item.path: item for item in files}
    result: list[Diagnostic] = []
    for row in rows:
        path = Path(row.filename)
        if path.is_absolute() and not path.is_relative_to(workspace):
            continue
        relative = path.relative_to(workspace).as_posix() if path.is_absolute() else path.as_posix()
        item = by_path.get(relative)
        if item is not None:
            candidates = [symbol for symbol in item.symbols if symbol.start_line <= row.location.row <= symbol.end_line]
            symbol = min(candidates, key=lambda candidate: candidate.end_line - candidate.start_line, default=None)
            result.append(Diagnostic(
                path=relative, sha256=item.sha256, code=row.code, line=row.location.row, column=row.location.column,
                end_line=row.end_location.row, end_column=row.end_location.column, message=row.message[:600],
                symbol=None if symbol is None else symbol.name,
            ))
    return tuple(result)
