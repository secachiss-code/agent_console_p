"""Python imports read with `ast`, in the core's own process. No project code runs and no tool is needed."""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Final

from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph, GraphEdge, GraphNode

_SKIP: Final = frozenset({"__pycache__", "node_modules", "venv", "site-packages", "build", "dist"})
_FILE_LIMIT: Final = 5000
_INIT: Final = "__init__"
_SOURCES: Final = "src"


def scan_python(root: Path) -> ExtractorGraph:
    """Modules under ``root`` and the imports between them. A file that does not parse is a node with no edges."""
    modules = _modules(root)
    edges: set[tuple[str, str]] = set()
    for name, path in modules.items():
        for target in _imports(root / path, name, modules, is_package=Path(path).stem == _INIT):
            if target != name:
                edges.add((name, target))
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
            relative = (Path(directory) / file).relative_to(root)
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


def _imports(path: Path, module: str, modules: dict[str, str], *, is_package: bool) -> set[str]:
    try:
        tree = ast.parse(path.read_bytes())
    except (OSError, SyntaxError, ValueError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.update(_known(alias.name, modules))
        elif isinstance(node, ast.ImportFrom):
            base = _base(node, module, is_package=is_package)
            if base is None:
                continue
            for alias in node.names:
                joined = f"{base}.{alias.name}" if base else alias.name
                # `from pkg import mod` names a module; `from pkg import value` names something in `pkg`.
                found.update(_known(joined if joined in modules else base, modules))
    return found


def _base(node: ast.ImportFrom, module: str, *, is_package: bool) -> str | None:
    if node.level == 0:
        return node.module or ""
    parts = module.split(".")
    drop = node.level - 1 if is_package else node.level
    if drop > len(parts):
        return None
    anchor = parts[: len(parts) - drop]
    return ".".join((*anchor, node.module)) if node.module else ".".join(anchor)


def _known(name: str, modules: dict[str, str]) -> set[str]:
    """`pkg.mod.attr` imports `pkg.mod` when that is the deepest module there is."""
    parts = name.split(".")
    for length in range(len(parts), 0, -1):
        candidate = ".".join(parts[:length])
        if candidate in modules:
            return {candidate}
    return set()
