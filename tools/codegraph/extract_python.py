"""Print the import graph of one Python package as extractor JSON version 1.

``hostrun devtools python <path>`` runs this with the container's python3.
The path is a directory that contains exactly one package. grimp 3.17 supplies
the edges when it is installed. The devtools image has no grimp, so that run
walks ``import`` statements with ``ast`` and emits the same JSON.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        _ = sys.stderr.write("usage: extract_python.py <package-root>\n")
        return 2
    root = Path(argv[0])
    if not root.is_dir():
        _ = sys.stderr.write(f"not a directory: {root}\n")
        return 2
    try:
        payload = _graph(root)
    except ValueError as exc:
        _ = sys.stderr.write(f"{exc}\n")
        return 2
    _ = sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
    return 0


def _graph(root: Path) -> dict[str, object]:
    try:
        import grimp  # noqa: PLC0415 -- devtools python3 has no grimp; the ast walk is the fallback
    except ModuleNotFoundError:
        return _ast_graph(root)
    package = _package_name(root)
    inserted = str(root.resolve())
    added = inserted not in sys.path
    if added:
        sys.path.insert(0, inserted)
    try:
        graph = grimp.build_graph(package, cache_dir=None)
    finally:
        if added:
            sys.path.remove(inserted)
    names = tuple(sorted(graph.modules))
    return {
        "edges": [
            {"kind": "imports", "source": name, "target": imported}
            for name in names
            for imported in sorted(graph.find_modules_directly_imported_by(name))
        ],
        "nodes": [
            {"id": name, "kind": _kind(root, name), "name": name, "path": _module_path(root, name)} for name in names
        ],
        "version": 1,
    }


def _ast_graph(root: Path) -> dict[str, object]:
    """Same JSON when grimp cannot be imported. Used by the devtools image."""
    package = _package_name(root)
    files = sorted(path for path in root.rglob("*.py") if _inside(package, root, path))
    names = tuple(sorted(_module_name(root, path) for path in files))
    known = set(names)
    edges: list[dict[str, str]] = []
    for path in files:
        module = _module_name(root, path)
        for target in sorted(_ast_imports(root, module, path)):
            if target in known and target != module:
                edges.append({"kind": "imports", "source": module, "target": target})
    return {
        "edges": edges,
        "nodes": [
            {"id": name, "kind": _kind(root, name), "name": name, "path": _module_path(root, name)} for name in names
        ],
        "version": 1,
    }


def _package_name(root: Path) -> str:
    packages = sorted(child.name for child in root.iterdir() if (child / "__init__.py").is_file())
    if len(packages) != 1:
        message = "the directory must contain exactly one Python package"
        raise ValueError(message)
    return packages[0]


def _inside(package: str, root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    return relative.parts[0] == package


def _module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    if relative.name == "__init__.py":
        return ".".join(relative.parent.parts)
    return ".".join(relative.with_suffix("").parts)


def _module_path(root: Path, module: str) -> str:
    package = root.joinpath(*module.split("."))
    if (package / "__init__.py").is_file():
        return (package / "__init__.py").relative_to(root).as_posix()
    return package.with_suffix(".py").relative_to(root).as_posix()


def _kind(root: Path, module: str) -> str:
    if (root.joinpath(*module.split(".")) / "__init__.py").is_file():
        return "package"
    return "module"


def _is_module(root: Path, name: str) -> bool:
    if name == "":
        return False
    base = root.joinpath(*name.split("."))
    return (base / "__init__.py").is_file() or base.with_suffix(".py").is_file()


def _ast_imports(root: Path, module: str, path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(_import_names(root, node))
        elif isinstance(node, ast.ImportFrom):
            found.update(_from_names(root, module, node))
    return found


def _import_names(root: Path, node: ast.Import) -> set[str]:
    found: set[str] = set()
    for alias in node.names:
        if _is_module(root, alias.name):
            found.add(alias.name)
    return found


def _from_names(root: Path, module: str, node: ast.ImportFrom) -> set[str]:
    base = _from_base(root, module, node)
    if base is None or not _is_module(root, base):
        return set()
    found: set[str] = set()
    for alias in node.names:
        candidate = f"{base}.{alias.name}"
        if _is_module(root, candidate):
            found.add(candidate)
        else:
            found.add(base)
    return found


def _from_base(root: Path, module: str, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    package = module if _kind(root, module) == "package" else module.rpartition(".")[0]
    parts = package.split(".") if package else []
    if node.level > len(parts):
        return None
    kept = parts[: len(parts) - (node.level - 1)]
    prefix = ".".join(kept)
    if node.module is None:
        return prefix or None
    if prefix == "":
        return node.module
    return f"{prefix}.{node.module}"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
