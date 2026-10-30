"""Turn dependency-cruiser JSON into the extractor protocol.

Only a resolved path that is itself a module in the document becomes an edge.
A core module such as ``fs`` is not a node.
"""

from __future__ import annotations

from pathlib import Path

import msgspec

from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph, GraphEdge, GraphNode

_SCHEMA = "dependency-cruiser output does not match the schema"


class CruiserDependency(msgspec.Struct, frozen=True, kw_only=True):
    """One dependency. Extra cruiser fields are ignored."""

    resolved: str | None = None
    could_not_resolve: bool = msgspec.field(name="couldNotResolve", default=False)


class CruiserModule(msgspec.Struct, frozen=True, kw_only=True):
    """One source file and the dependencies cruiser recorded."""

    source: str
    dependencies: tuple[CruiserDependency, ...] = ()


class CruiserDocument(msgspec.Struct, frozen=True, kw_only=True):
    """The ``modules`` array of a dependency-cruiser JSON document."""

    modules: tuple[CruiserModule, ...]


def from_cruiser(raw: bytes) -> ExtractorGraph:
    """Protocol graph for one dependency-cruiser document."""
    try:
        document = msgspec.json.decode(raw, type=CruiserDocument)
    except msgspec.DecodeError as exc:
        raise ValueError(_SCHEMA) from exc
    sources = tuple(sorted(module.source for module in document.modules))
    known = set(sources)
    edges = tuple(
        GraphEdge(source=module.source, target=resolved, kind="imports")
        for module in document.modules
        for resolved in _resolved(module)
        if resolved in known
    )
    ordered = tuple(sorted(edges, key=lambda edge: (edge.source, edge.target)))
    nodes = tuple(GraphNode(id=source, path=source, name=Path(source).stem, kind="module") for source in sources)
    return ExtractorGraph(version=1, nodes=nodes, edges=ordered)


def _resolved(module: CruiserModule) -> tuple[str, ...]:
    found: list[str] = []
    for dependency in module.dependencies:
        resolved = dependency.resolved
        if dependency.could_not_resolve or resolved is None or resolved == "":
            continue
        found.append(resolved)
    return tuple(found)
