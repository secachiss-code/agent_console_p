"""Python imports read with `ast`, in the core's own process. No project code runs and no tool is needed."""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph, GraphEdge, GraphNode

_SKIP: Final = frozenset({"__pycache__", "node_modules", "venv", "site-packages", "build", "dist"})
_FILE_LIMIT: Final = 5000
_INIT: Final = "__init__"
_SOURCES: Final = "src"


@dataclass(frozen=True, slots=True)
class PythonImports:
    stamp: tuple[int, int, int, int, int]
    absolute: tuple[str, ...]
    relative: tuple[tuple[int, str | None, tuple[str, ...]], ...]


def scan_python(root: Path, *, cache: dict[str, PythonImports] | None = None) -> ExtractorGraph:
    """Modules under ``root`` and the imports between them. A file that does not parse is a node with no edges."""
    modules = _modules(root)
    edges: set[tuple[str, str]] = set()
    updated: dict[str, PythonImports] = {}
    for name, path in modules.items():
        held = None if cache is None else cache.get(path)
        imports = _read_imports(root / path, held)
        if imports is None:
            continue
        updated[path] = imports
        for target in _imports(imports, name, modules, is_package=Path(path).stem == _INIT):
            if target != name:
                edges.add((name, target))
    if cache is not None:
        cache.clear()
        cache.update(updated)
    return ExtractorGraph(
        version=1,
        nodes=tuple(
            GraphNode(id=name, path=path, name=name.rpartition(".")[2], kind="module")
            for name, path in sorted(modules.items())
        ),
        edges=tuple(GraphEdge(source=source, target=target, kind="imports") for source, target in sorted(edges)),
    )


def _modules(root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = sorted(item for item in subdirectories if not _skipped(item))
        for file in sorted(files):
            if not file.endswith(".py"):
                continue
            candidate = Path(directory) / file
            if candidate.is_symlink():
                continue
            relative = candidate.relative_to(root)
            found[_module_name(relative)] = relative.as_posix()
            if len(found) >= _FILE_LIMIT:
                return found
    return found


def _skipped(directory: str) -> bool:
    return directory.startswith(".") or directory in _SKIP


def _module_name(relative: Path) -> str:
    """`src/pkg/mod.py` and `pkg/mod.py` are both `pkg.mod`."""
    parts = list(relative.with_suffix("").parts)
    if parts[0] == _SOURCES and len(parts) > 1:
        parts = parts[1:]
    if parts[-1] == _INIT and len(parts) > 1:
        parts = parts[:-1]
    return ".".join(parts)


def _read_imports(path: Path, held: PythonImports | None) -> PythonImports | None:
    try:
        info = path.stat()
        stamp = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if held is not None and held.stamp == stamp:
            return held
        tree = ast.parse(path.read_bytes())
    except (OSError, SyntaxError, ValueError):
        return None
    absolute: list[str] = []
    relative: list[tuple[int, str | None, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            absolute.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            relative.append((node.level, node.module, tuple(alias.name for alias in node.names)))
    return PythonImports(stamp, tuple(absolute), tuple(relative))


def _imports(imports: PythonImports, module: str, modules: dict[str, str], *, is_package: bool) -> set[str]:
    found: set[str] = set()
    for name in imports.absolute:
        found.update(_known(name, modules))
    for level, imported, names in imports.relative:
        base = _base(level, imported, module, is_package=is_package)
        if base is None:
            continue
        for name in names:
            joined = f"{base}.{name}" if base else name
            # `from pkg import mod` names a module; `from pkg import value` names something in `pkg`.
            found.update(_known(joined if joined in modules else base, modules))
    return found


def _base(level: int, imported: str | None, module: str, *, is_package: bool) -> str | None:
    if level == 0:
        return imported or ""
    parts = module.split(".")
    drop = level - 1 if is_package else level
    if drop > len(parts):
        return None
    anchor = parts[: len(parts) - drop]
    return ".".join((*anchor, imported)) if imported else ".".join(anchor)


def _known(name: str, modules: dict[str, str]) -> set[str]:
    """`pkg.mod.attr` imports `pkg.mod` when that is the deepest module there is."""
    parts = name.split(".")
    for length in range(len(parts), 0, -1):
        candidate = ".".join(parts[:length])
        if candidate in modules:
            return {candidate}
    return set()
