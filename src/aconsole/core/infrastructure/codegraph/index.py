"""Which modules a change reaches. Python is read locally; other languages go through the devtools extractors."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, final

import msgspec

from aconsole.contracts.errors import ErrorObject
from aconsole.core.application.ports.plan_store import StoreWriteError
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.codegraph.local_python import PythonImports, scan_python
from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph, prefix_graph
from aconsole.core.infrastructure.codegraph.runner import ExtractorRun
from aconsole.core.infrastructure.codegraph.watch import SourceWatch

_LOG = logging.getLogger(__name__)
_TTL_S: Final = 300.0
_FAILED: Final = 5006
_UNKNOWN_PROJECT: Final = 5007
_PYTHON_MARKERS: Final = ("pyproject.toml", "setup.py", "setup.cfg")
_FOREIGN: Final = (("rust", "rust-metadata", "rs:"), ("typescript", "ts", "ts:"), ("csharp", "csharp", "cs:"))


class Runner(Protocol):
    """What the index needs from `ExtractorRunner`."""

    def run(self, language: str, path: str, *, cwd: Path | None = None) -> ExtractorRun: ...


class GraphStore(Protocol):
    def sync(self, project_id: str, graph: ExtractorGraph) -> None:
        """Commit a graph snapshot before it is published to callers."""
        ...


@dataclass(frozen=True, slots=True)
class _Cached:
    graph: ExtractorGraph
    until: float
    languages: frozenset[str]
    generation: int
    revision: str
    failed_languages: tuple[str, ...]
    ids: dict[str, str]
    names: dict[str, tuple[str, ...]]
    users: dict[frozenset[str] | None, dict[str, tuple[str, ...]]]


@final
class CodeGraphIndex:
    """Keep project and worktree snapshots separate, even when module IDs match."""

    def __init__(  # noqa: PLR0913 -- optional persistence extends the existing index configuration
        self,
        runner: Runner,
        root_of: Callable[[str], Path | None],
        *,
        clock: Callable[[], float] = time.monotonic,
        ttl_s: float = _TTL_S,
        languages: Callable[[], frozenset[str]] | None = None,
        store: GraphStore | None = None,
    ) -> None:
        self._store = store
        self._runner: Runner = runner
        self._root_of: Callable[[str], Path | None] = root_of
        self._clock: Callable[[], float] = clock
        self._ttl_s: float = ttl_s
        self._languages: Callable[[], frozenset[str]] = languages or (
            lambda: frozenset({"python", "rust", "typescript", "csharp"})
        )
        self._cache: dict[tuple[Path, str], _Cached] = {}
        self._lock: threading.Lock = threading.Lock()
        self._builds: dict[Path, threading.Lock] = {}
        self._python: dict[Path, dict[str, PythonImports]] = {}
        self._generations: dict[tuple[Path, str], int] = {}
        self._dirty: set[tuple[Path, str]] = set()
        self._watch: SourceWatch | None = None

    def start_watching(self) -> None:
        self._watch = SourceWatch(self._file_changed)

    def stop_watching(self) -> None:
        watch = self._watch
        self._watch = None
        if watch is not None:
            watch.stop()

    def _file_changed(self, root: Path) -> None:
        self._invalidate(lambda key: key[0] == root)

    def invalidate_project(self, project_id: str) -> None:
        self._invalidate(lambda key: key[1] == project_id)

    def _invalidate(self, matches: Callable[[tuple[Path, str]], bool]) -> None:
        with self._lock:
            for key in self._generations:
                if matches(key):
                    self._generations[key] += 1
                    self._dirty.add(key)

    def refresh_changed(self) -> None:
        """Coalesce pending events and refresh watched roots off the event loop."""
        with self._lock:
            dirty = sorted(self._dirty)
            self._dirty.clear()
        for root, project_id in dirty:
            if not root.is_dir():
                continue
            try:
                _ = self.graph(project_id, worktree=root)
            except (DomainError, OSError, StoreWriteError) as exc:
                _LOG.warning("code graph refresh failed for %s: %s", root, exc)

    @property
    def store(self) -> GraphStore | None:
        """The persistence adapter attached during bootstrap."""
        return self._store

    def use_store(self, store: GraphStore) -> None:
        """Attach durable graph storage during startup and rebuild prior cached snapshots."""
        with self._lock:
            self._store = store
            self._cache.clear()

    def graph(self, project_id: str, *, worktree: Path | None = None) -> ExtractorGraph:
        return self._cached(project_id, worktree).graph

    def describe(self, project_id: str, *, worktree: Path | None = None) -> tuple[str, tuple[str, ...]]:
        """Identify the topology and report extractors missing from this snapshot."""
        cached = self._cached(project_id, worktree)
        return cached.revision, cached.failed_languages

    def snapshot(self, project_id: str, *, worktree: Path | None = None) -> tuple[ExtractorGraph, str, tuple[str, ...]]:
        cached = self._cached(project_id, worktree)
        return cached.graph, cached.revision, cached.failed_languages

    def is_project_root(self, project_id: str, worktree: Path) -> bool:
        """Whether the project-wide SQLite snapshot represents this worktree."""
        registered = self._root_of(project_id)
        return registered is not None and registered.resolve() == worktree.resolve()

    def _cached(self, project_id: str, worktree: Path | None) -> _Cached:
        registered = self._root_of(project_id)
        if registered is None:
            raise DomainError(code=_UNKNOWN_PROJECT, name="IMPACT_TARGET_UNKNOWN", retryable=False)
        main = registered.resolve()
        root = main if worktree is None else worktree.resolve()
        key = (root, project_id)
        with self._lock:
            building = self._builds.setdefault(root, threading.Lock())
            _ = self._generations.setdefault(key, 0)
        # Callers that ask while a graph is being built wait for it instead of building their own.
        with building:
            if (watch := self._watch) is not None:
                watch.watch(root)
            enabled = self._languages()
            with self._lock:
                generation = self._generations[key]
                found = self._cache.get(key)
                if (
                    found is not None
                    and self._clock() < found.until
                    and found.languages == enabled
                    and found.generation == generation
                ):
                    return found
            graph, failed = self._build(root, enabled)
            built = _indexed(graph, self._clock() + self._ttl_s, enabled, generation, failed)
            if self._store is not None and root == main:
                self._store.sync(project_id, built.graph)
            with self._lock:
                self._cache[key] = built
            return built

    def resolve(self, project_id: str, target: str, *, worktree: Path | None = None) -> str | None:
        """A node id from an id, a path, or a name only one module has."""
        cached = self._cached(project_id, worktree)
        found = cached.ids.get(target)
        if found is not None:
            return found
        named = cached.names.get(target, ())
        return named[0] if len(named) == 1 else None

    def dependents(
        self, project_id: str, kinds: frozenset[str] | None = None, *, worktree: Path | None = None
    ) -> dict[str, tuple[str, ...]]:
        """For each node, the nodes that use it: a change spreads against the import arrows."""
        cached = self._cached(project_id, worktree)
        with self._lock:
            found = cached.users.get(kinds)
        if found is not None:
            return found
        reversed_edges = reverse_edges(cached.graph, kinds)
        with self._lock:
            cached.users[kinds] = reversed_edges
        return reversed_edges

    def _build(self, root: Path, enabled: frozenset[str]) -> tuple[ExtractorGraph, tuple[str, ...]]:
        detected = _project_languages(root)
        graphs: list[ExtractorGraph] = []
        errors: dict[str, ErrorObject | None] = {}
        if "python" in detected & enabled:
            with self._lock:
                parsed = self._python.setdefault(root, {})
            graphs.append(scan_python(root, cache=parsed))
        for language, extractor, prefix in _FOREIGN:
            if language not in detected & enabled:
                continue
            run = self._runner.run(extractor, ".", cwd=root)
            if run.graph is None:
                errors[language] = run.error
            else:
                graphs.append(prefix_graph(run.graph, prefix))
        if not graphs and errors:
            raise _failed(next(iter(errors.values())))
        graph = ExtractorGraph(
            version=1,
            nodes=tuple(sorted((node for graph in graphs for node in graph.nodes), key=lambda node: node.id)),
            edges=tuple(
                sorted(
                    (edge for graph in graphs for edge in graph.edges),
                    key=lambda edge: (edge.source, edge.target, edge.kind),
                )
            ),
        )
        return graph, tuple(sorted(errors))


def _project_languages(root: Path) -> frozenset[str]:
    languages: set[str] = set()
    if any((root / marker).is_file() for marker in _PYTHON_MARKERS):
        languages.add("python")
    if (root / "Cargo.toml").is_file():
        languages.add("rust")
    if any((root / marker).is_file() for marker in ("package.json", "tsconfig.json")):
        languages.add("typescript")
    if any(path.is_file() for pattern in ("*.sln", "*.csproj") for path in root.glob(pattern)):
        languages.add("csharp")
    return frozenset(languages or {"python"})


def _indexed(
    graph: ExtractorGraph, until: float, languages: frozenset[str], generation: int, failed: tuple[str, ...]
) -> _Cached:
    ids: dict[str, str] = {}
    names: dict[str, list[str]] = {}
    for node in graph.nodes:
        # An id wins over a path that happens to spell the same text, as the scan in node order did.
        _ = ids.setdefault(node.id, node.id)
        _ = ids.setdefault(node.path, node.id)
        names.setdefault(node.name, []).append(node.id)
    return _Cached(
        graph=graph,
        until=until,
        languages=languages,
        generation=generation,
        revision=hashlib.sha256(msgspec.json.encode(graph)).hexdigest(),
        failed_languages=failed,
        ids=ids,
        names={name: tuple(found) for name, found in names.items()},
        users={},
    )


def _failed(error: ErrorObject | None) -> DomainError:
    detail = "" if error is None else error.message
    return DomainError(code=_FAILED, name="CODEGRAPH_EXTRACTOR_FAILED", retryable=True, extra=(detail,))


def reverse_edges(graph: ExtractorGraph, kinds: frozenset[str] | None = None) -> dict[str, tuple[str, ...]]:
    users: dict[str, set[str]] = {}
    for edge in graph.edges:
        if kinds is None or edge.kind in kinds:
            users.setdefault(edge.target, set()).add(edge.source)
    return {key: tuple(sorted(values)) for key, values in users.items()}


def impact_layers(
    edges: dict[str, tuple[str, ...]],
    start: str,
    hops: int,
    limit: int,
    *,
    additional_roots: tuple[str, ...] = (),
) -> tuple[list[list[str]], bool]:
    """Walk reverse edges from a single target, visiting each dependent once."""
    frontier = [start, *additional_roots]
    seen = set(frontier)
    layers: list[list[str]] = []
    remaining = limit
    for _hop in range(hops):
        layer: list[str] = []
        for node in frontier:
            for linked in edges.get(node, ()):
                if linked in seen:
                    continue
                if remaining == 0:
                    if layer:
                        layers.append(layer)
                    return layers, True
                seen.add(linked)
                layer.append(linked)
                remaining -= 1
        if not layer:
            break
        layers.append(layer)
        frontier = layer
    return layers, False
