"""Build an extractor graph from a grimp import graph.

External modules stay out. grimp's default already omits them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import grimp

from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph, GraphEdge, GraphNode

_ONE = "the directory must contain exactly one Python package"


def build_python_graph(root: Path) -> ExtractorGraph:
    """Import-graph of the single package under ``root``."""
    package = _package_name(root)
    inserted = str(root)
    added = inserted not in sys.path
    if added:
        sys.path.insert(0, inserted)
    try:
        graph = grimp.build_graph(package, cache_dir=None)
    finally:
        if added:
            sys.path.remove(inserted)
    return from_grimp(graph, root=root)


def from_grimp(graph: grimp.ImportGraph, *, root: Path) -> ExtractorGraph:
    """Protocol graph for modules grimp already found."""
    names = tuple(sorted(graph.modules))
    nodes = tuple(
        GraphNode(id=name, path=_module_path(root, name), name=name, kind=_kind(root, name)) for name in names
    )
    edges = tuple(
        GraphEdge(source=name, target=imported, kind="imports")
        for name in names
        for imported in sorted(graph.find_modules_directly_imported_by(name))
    )
    return ExtractorGraph(version=1, nodes=nodes, edges=edges)


def _package_name(root: Path) -> str:
    packages = sorted(child.name for child in root.iterdir() if (child / "__init__.py").is_file())
    if len(packages) != 1:
        raise ValueError(_ONE)
    return packages[0]


def _module_path(root: Path, module: str) -> str:
    parts = module.split(".")
    package = root.joinpath(*parts)
    if (package / "__init__.py").is_file():
        return (package / "__init__.py").relative_to(root).as_posix()
    return package.with_suffix(".py").relative_to(root).as_posix()


def _kind(root: Path, module: str) -> str:
    package = root.joinpath(*module.split("."))
    if (package / "__init__.py").is_file():
        return "package"
    return "module"
