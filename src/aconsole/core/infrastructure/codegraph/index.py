"""Which modules a change reaches. Python is read locally; other languages go through the devtools extractors."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, final

from aconsole.contracts.errors import ErrorObject
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.codegraph.local_python import scan_python
from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph, prefix_graph
from aconsole.core.infrastructure.codegraph.runner import ExtractorRun

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
    ids: dict[str, str]
    names: dict[str, tuple[str, ...]]
    users: dict[frozenset[str] | None, dict[str, tuple[str, ...]]]


@final
class CodeGraphIndex:
    """One graph per project root, kept a few minutes: building one can take a minute."""

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
        self._cached_languages: dict[Path, frozenset[str]] = {}
        self._cache: dict[tuple[Path, str], _Cached] = {}
        self._lock: threading.Lock = threading.Lock()
        self._builds: dict[Path, threading.Lock] = {}

    @property
    def store(self) -> GraphStore | None:
        """The persistence adapter attached during bootstrap."""
        return self._store

    def use_store(self, store: GraphStore) -> None:
        """Attach durable graph storage during startup and rebuild prior cached snapshots."""
        with self._lock:
            self._store = store
            self._cache.clear()
            self._cached_languages.clear()

    def graph(self, project_id: str) -> ExtractorGraph:
        return self._cached(project_id).graph

    def _cached(self, project_id: str) -> _Cached:
        root = self._root_of(project_id)
        if root is None:
            raise DomainError(code=_UNKNOWN_PROJECT, name="IMPACT_TARGET_UNKNOWN", retryable=False)
        enabled = self._languages()
        with self._lock:
            building = self._builds.setdefault(root, threading.Lock())
        # Callers that ask while a graph is being built wait for it instead of building their own.
        with building:
            with self._lock:
                found = self._cache.get((root, project_id))
                if found is not None and self._clock() < found.until and self._cached_languages.get(root) == enabled:
                    return found
            built = _indexed(self._build(root, enabled), self._clock() + self._ttl_s)
            if self._store is not None:
                self._store.sync(project_id, built.graph)
            with self._lock:
                self._cache[(root, project_id)] = built
                self._cached_languages[root] = enabled
            return built

    def resolve(self, project_id: str, target: str) -> str | None:
        """A node id from an id, a path, or a name only one module has."""
        cached = self._cached(project_id)
        found = cached.ids.get(target)
        if found is not None:
            return found
        named = cached.names.get(target, ())
        return named[0] if len(named) == 1 else None

    def dependents(self, project_id: str, kinds: frozenset[str] | None = None) -> dict[str, tuple[str, ...]]:
        """For each node, the nodes that use it: a change spreads against the import arrows."""
        cached = self._cached(project_id)
        with self._lock:
            found = cached.users.get(kinds)
        if found is not None:
            return found
        users: dict[str, list[str]] = {}
        for edge in cached.graph.edges:
            if kinds is None or edge.kind in kinds:
                users.setdefault(edge.target, []).append(edge.source)
        reversed_edges = {key: tuple(sorted(values)) for key, values in users.items()}
        with self._lock:
            cached.users[kinds] = reversed_edges
        return reversed_edges

    def _build(self, root: Path, enabled: frozenset[str]) -> ExtractorGraph:
        detected = _project_languages(root)
        graphs: list[ExtractorGraph] = []
        errors: list[ErrorObject | None] = []
        if "python" in detected & enabled:
            graphs.append(scan_python(root))
        for language, extractor, prefix in _FOREIGN:
            if language not in detected & enabled:
                continue
            run = self._runner.run(extractor, ".", cwd=root)
            if run.graph is None:
                errors.append(run.error)
            else:
                graphs.append(prefix_graph(run.graph, prefix))
        if not graphs and errors:
            raise _failed(errors[0])
        return ExtractorGraph(
            version=1,
            nodes=tuple(sorted((node for graph in graphs for node in graph.nodes), key=lambda node: node.id)),
            edges=tuple(
                sorted(
                    (edge for graph in graphs for edge in graph.edges),
                    key=lambda edge: (edge.source, edge.target, edge.kind),
                )
            ),
        )


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


def _indexed(graph: ExtractorGraph, until: float) -> _Cached:
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
        ids=ids,
        names={name: tuple(found) for name, found in names.items()},
        users={},
    )


def _failed(error: ErrorObject | None) -> DomainError:
    detail = "" if error is None else error.message
    return DomainError(code=_FAILED, name="CODEGRAPH_EXTRACTOR_FAILED", retryable=True, extra=(detail,))
