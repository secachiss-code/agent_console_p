"""Turn dependency-cruiser JSON into the extractor protocol.

Only a resolved path that is itself a module in the document becomes an edge.
A core module such as ``fs`` is not a node.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import final

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
    sources = tuple(sorted({module.source for module in document.modules if _local_path(module.source)}))
    known = set(sources)
    links = {
        (module.source, resolved)
        for module in document.modules
        for resolved in _resolved(module)
        if module.source in known and resolved in known
    }
    ordered = tuple(GraphEdge(source=source, target=target, kind="imports") for source, target in sorted(links))
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


def _local_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and not ({"..", "node_modules"} & set(path.parts))


def extract_typescript(node: Path, cruiser: Path, path: str, *, root: Path) -> ExtractorGraph:
    """Require the image's pinned CLI and ignore executable project cruise configs."""
    if not node.is_absolute() or not cruiser.is_absolute() or not node.is_file() or not cruiser.is_file():
        message = "preinstalled TypeScript extractor is unavailable"
        raise ValueError(message)
    target = root / path
    if not _local_path(path) or not target.resolve().is_relative_to(root.resolve()) or not target.exists():
        message = "TypeScript extraction path must stay inside the worktree"
        raise ValueError(message)
    command = (str(node), str(cruiser))
    version = subprocess.run(  # noqa: S603 -- image-owned tool argv, never a shell command
        (*command, "--version"),
        cwd=root,
        check=True,
        capture_output=True,
        timeout=5,
    )
    if version.stdout.strip() != b"18.7.0":
        message = "dependency-cruiser 18.7.0 is required"
        raise ValueError(message)
    args = [
        *command,
        "--no-config",
        "--output-type",
        "json",
        "--do-not-follow",
        "(^|/)node_modules/",
        "--exclude",
        "(^|/)node_modules/",
        "--ts-pre-compilation-deps",
    ]
    config = root / "tsconfig.json"
    if config.is_file():
        args.extend(("--ts-config", str(config)))
    args.extend(("--", path))
    result = subprocess.run(  # noqa: S603 -- validated path is a separate positional argument
        args,
        cwd=root,
        check=True,
        capture_output=True,
        timeout=45,
    )
    return from_cruiser(result.stdout)


@final
class _Arguments(argparse.Namespace):
    node: Path = Path()
    cruiser: Path = Path()
    path: str = "."


def main(argv: list[str] | None = None) -> int:
    """External hostrun adapter: cruiser stdout becomes graph protocol v1."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--node", required=True, type=Path)
    _ = parser.add_argument("--cruiser", required=True, type=Path)
    _ = parser.add_argument("path")
    args = parser.parse_args(argv, namespace=_Arguments())
    try:
        graph = extract_typescript(args.node, args.cruiser, args.path, root=Path.cwd())
    except subprocess.CalledProcessError as exc:
        # Captured diagnostics belong on stderr, never on the JSON channel.
        _ = sys.stderr.write(f"TypeScript extractor exited with status {exc.returncode}\n")
        return 1
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        _ = sys.stderr.write(str(exc)[-400:] + "\n")
        return 1
    _ = sys.stdout.buffer.write(msgspec.json.encode(graph) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
