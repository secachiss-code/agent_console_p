"""Convert Cargo metadata and a documented subset of cargo-modules DOT to graphs.

The DOT reader accepts quoted node IDs and directed edges in an unnamed digraph.
It does not claim to parse arbitrary DOT or unverified cargo-modules attributes.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

import msgspec

from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph, GraphEdge, GraphNode

_SCHEMA = "Cargo metadata does not match the schema"
_DOT = "unsupported or malformed cargo-modules DOT"
_MIN_TOKENS = 3
_TOKEN = re.compile(r'\s*(?:(//[^\n]*)|("(?:[^"\\]|\\["\\])*")|(->)|([{};\[\]=,])|([A-Za-z0-9_.]+))')


class _Dependency(msgspec.Struct, frozen=True, kw_only=True):
    name: str
    path: str | None = None


class _Package(msgspec.Struct, frozen=True, kw_only=True):
    id: str
    name: str
    manifest_path: str
    dependencies: tuple[_Dependency, ...]


class _Metadata(msgspec.Struct, frozen=True, kw_only=True):
    packages: tuple[_Package, ...]
    workspace_members: tuple[str, ...]
    workspace_root: str


def from_cargo_metadata(raw: bytes) -> ExtractorGraph:
    """Convert workspace members and their local dependencies to a graph."""
    try:
        metadata = msgspec.json.decode(raw, type=_Metadata)
    except msgspec.DecodeError as exc:
        raise ValueError(_SCHEMA) from exc
    root = PurePosixPath(metadata.workspace_root)
    if not root.is_absolute():
        raise ValueError(_SCHEMA)
    members = set(metadata.workspace_members)
    packages = tuple(package for package in metadata.packages if package.id in members)
    paths, by_directory, nodes = _package_nodes(packages, root)
    links: set[tuple[str, str]] = set()
    for package in packages:
        for dependency in package.dependencies:
            if dependency.path is not None:
                target = by_directory.get((str(PurePosixPath(dependency.path)), dependency.name))
                if target is not None:
                    links.add((paths[package.id], target))
    edges = tuple(GraphEdge(source=source, target=target, kind="depends_on") for source, target in sorted(links))
    return ExtractorGraph(version=1, nodes=tuple(sorted(nodes, key=lambda node: node.id)), edges=edges)


def _package_nodes(
    packages: tuple[_Package, ...], root: PurePosixPath
) -> tuple[dict[str, str], dict[tuple[str, str], str], list[GraphNode]]:
    paths: dict[str, str] = {}
    by_directory: dict[tuple[str, str], str] = {}
    nodes: list[GraphNode] = []
    for package in packages:
        manifest = PurePosixPath(package.manifest_path)
        try:
            relative = manifest.relative_to(root)
        except ValueError as exc:
            raise ValueError(_SCHEMA) from exc
        if not manifest.is_absolute() or ".." in relative.parts:
            raise ValueError(_SCHEMA)
        identifier = relative.as_posix()
        if identifier in paths.values() or package.id in paths:
            raise ValueError(_SCHEMA)
        paths[package.id] = identifier
        by_directory[(str(manifest.parent), package.name)] = identifier
        nodes.append(GraphNode(id=identifier, path=identifier, name=package.name, kind="package"))
    return paths, by_directory, nodes


def from_cargo_modules_dot(raw: bytes) -> ExtractorGraph:
    """Parse quoted IDs and ownership edges in the supported unnamed digraph subset."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(_DOT) from exc
    tokens: list[str] = []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:
            if text[position:].strip() == "":
                break
            raise ValueError(_DOT)
        if match.group(1) is None:
            tokens.append(match.group(0).strip())
        position = match.end()
    if len(tokens) < _MIN_TOKENS or tokens[:2] != ["digraph", "{"] or tokens[-1] != "}":
        raise ValueError(_DOT)
    nodes, links = _dot_statements(tokens)
    if any(source not in nodes or target not in nodes for source, target, _kind in links):
        raise ValueError(_DOT)
    graph_nodes = tuple(
        GraphNode(id=node, path=node, name=node.rsplit("::", 1)[-1], kind="module") for node in sorted(nodes)
    )
    edges = tuple(GraphEdge(source=source, target=target, kind=kind) for source, target, kind in sorted(links))
    return ExtractorGraph(version=1, nodes=graph_nodes, edges=edges)


def _dot_statements(tokens: list[str]) -> tuple[set[str], set[tuple[str, str, str]]]:
    nodes: set[str] = set()
    links: set[tuple[str, str, str]] = set()
    position = 2
    while position < len(tokens) - 1:
        token = tokens[position]
        position += 1
        if token in {"graph", "node", "edge"}:
            _, position = _attributes(tokens, position)
        elif token.startswith('"'):
            source = _dot_id(token)
            if position < len(tokens) - 1 and tokens[position] == "->":
                position += 1
                if position >= len(tokens) - 1 or not tokens[position].startswith('"'):
                    raise ValueError(_DOT)
                target = _dot_id(tokens[position])
                attrs, position = _attributes(tokens, position + 1)
                kind = attrs.get("label", "owns")
                if kind not in {"owns", "uses"}:
                    raise ValueError(_DOT)
                links.add((source, target, kind))
            else:
                _, position = _attributes(tokens, position)
                nodes.add(source)
        else:
            raise ValueError(_DOT)
        if position < len(tokens) - 1 and tokens[position] == ";":
            position += 1
        elif position < len(tokens) - 1 and token not in {"graph", "node", "edge"}:
            raise ValueError(_DOT)
    return nodes, links


def _attributes(tokens: list[str], position: int) -> tuple[dict[str, str], int]:
    attrs: dict[str, str] = {}
    while position < len(tokens) - 1 and tokens[position] == "[":
        position += 1
        while position < len(tokens) - 1 and tokens[position] != "]":
            if position + 2 >= len(tokens) - 1 or tokens[position + 1] != "=":
                raise ValueError(_DOT)
            key, value = tokens[position], tokens[position + 2]
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError(_DOT)
            if value in {"[", "]", "{", "}", ";", ",", "=", "->"}:
                raise ValueError(_DOT)
            attrs[key] = _dot_id(value) if value.startswith('"') else value
            position += 3
            if position < len(tokens) - 1 and tokens[position] == ",":
                position += 1
            elif position < len(tokens) - 1 and tokens[position] != "]":
                raise ValueError(_DOT)
        if position >= len(tokens) - 1 or tokens[position] != "]":
            raise ValueError(_DOT)
        position += 1
    return attrs, position


def _dot_id(token: str) -> str:
    return re.sub(r'\\(["\\])', lambda match: match.group(1), token[1:-1])
