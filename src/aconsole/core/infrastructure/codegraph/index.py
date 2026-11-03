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
    ) -> None:
        self._runner: Runner = runner
        self._root_of: Callable[[str], Path | None] = root_of
        self._clock: Callable[[], float] = clock
        self._ttl_s: float = ttl_s
        self._cache: dict[Path, _Cached] = {}
        self._lock: threading.Lock = threading.Lock()

    def graph(self, project_id: str) -> ExtractorGraph:
        root = self._root_of(project_id)
        if root is None:
            raise DomainError(code=_UNKNOWN_PROJECT, name="IMPACT_TARGET_UNKNOWN", retryable=False)
        with self._lock:
            found = self._cache.get(root)
            if found is not None and self._clock() < found.until:
                return found.graph
        built = self._build(root)
        with self._lock:
            self._cache[root] = _Cached(built, self._clock() + self._ttl_s)
        return built

    def resolve(self, project_id: str, target: str) -> str | None:
        """A node id from an id, a path, or a name only one module has."""
        graph = self.graph(project_id)
        for node in graph.nodes:
            if target in {node.id, node.path}:
                return node.id
        named = [node.id for node in graph.nodes if node.name == target]
        return named[0] if len(named) == 1 else None

    def dependents(self, project_id: str, kinds: frozenset[str] | None = None) -> dict[str, tuple[str, ...]]:
        """For each node, the nodes that use it: a change spreads against the import arrows."""
        users: dict[str, list[str]] = {}
        for edge in self.graph(project_id).edges:
            if kinds is None or edge.kind in kinds:
                users.setdefault(edge.target, []).append(edge.source)
        return {key: tuple(sorted(values)) for key, values in users.items()}

    def _build(self, root: Path) -> ExtractorGraph:
        # A Python project with a `package.json` for its docs is still a Python project.
        if any((root / marker).is_file() for marker in _PYTHON_MARKERS):
            return scan_python(root)
        language = next((name for marker, name in _MARKERS if (root / marker).is_file()), None)
        if language is None:
            return scan_python(root)
        run = self._runner.run(language, ".", cwd=root)
        if run.graph is not None:
            return run.graph
        raise _failed(run.error)


def _failed(error: ErrorObject | None) -> DomainError:
    detail = "" if error is None else error.message
    return DomainError(code=_FAILED, name="CODEGRAPH_EXTRACTOR_FAILED", retryable=True, extra=(detail,))
