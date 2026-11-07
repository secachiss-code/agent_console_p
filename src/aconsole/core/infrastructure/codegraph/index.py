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
from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph
from aconsole.core.infrastructure.codegraph.runner import ExtractorRun

_TTL_S: Final = 300.0
_FAILED: Final = 5006
_UNKNOWN_PROJECT: Final = 5007
_PYTHON_MARKERS: Final = ("pyproject.toml", "setup.py", "setup.cfg")
_MARKERS: Final = (("Cargo.toml", "rust-metadata"), ("package.json", "ts"), ("tsconfig.json", "ts"))


class Runner(Protocol):
    """What the index needs from `ExtractorRunner`."""

    def run(self, language: str, path: str, *, cwd: Path | None = None) -> ExtractorRun: ...


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

    def __init__(
        self,
        runner: Runner,
        root_of: Callable[[str], Path | None],
        *,
        clock: Callable[[], float] = time.monotonic,
        ttl_s: float = _TTL_S,
        languages: Callable[[], frozenset[str]] | None = None,
    ) -> None:
        self._runner: Runner = runner
        self._root_of: Callable[[str], Path | None] = root_of
        self._clock: Callable[[], float] = clock
        self._ttl_s: float = ttl_s
        self._languages: Callable[[], frozenset[str]] = languages or (
            lambda: frozenset({"python", "rust", "typescript", "csharp"})
        )
        self._cached_languages: dict[Path, frozenset[str]] = {}
        self._cache: dict[Path, _Cached] = {}
        self._lock: threading.Lock = threading.Lock()
        self._builds: dict[Path, threading.Lock] = {}

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
                found = self._cache.get(root)
                if found is not None and self._clock() < found.until and self._cached_languages.get(root) == enabled:
                    return found
            built = _indexed(self._build(root, enabled), self._clock() + self._ttl_s)
            with self._lock:
                self._cache[root] = built
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
        # A Python project with a `package.json` for its docs is still a Python project.
        if any((root / marker).is_file() for marker in _PYTHON_MARKERS):
            return scan_python(root) if "python" in enabled else ExtractorGraph(version=1, nodes=(), edges=())
        language = next((name for marker, name in _MARKERS if (root / marker).is_file()), None)
        if language is None:
            return scan_python(root) if "python" in enabled else ExtractorGraph(version=1, nodes=(), edges=())
        if {"rust-metadata": "rust", "ts": "typescript"}[language] not in enabled:
            return ExtractorGraph(version=1, nodes=(), edges=())
        run = self._runner.run(language, ".", cwd=root)
        if run.graph is not None:
            return run.graph
        raise _failed(run.error)


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
