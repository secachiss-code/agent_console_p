"""Asking for one project's graph at once builds it once, and later questions do not walk it again."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from aconsole.core.infrastructure.codegraph.index import CodeGraphIndex
from aconsole.core.infrastructure.codegraph.local_python import scan_python
from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph, GraphEdge, GraphNode
from aconsole.core.infrastructure.codegraph.runner import ExtractorRun

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest


def _project(tmp_path: Path) -> Path:
    _ = (tmp_path / "pyproject.toml").write_text("")
    _ = (tmp_path / "a.py").write_text("import b\n")
    _ = (tmp_path / "b.py").write_text("")
    return tmp_path


def _index(root: Path, languages: Callable[[], frozenset[str]] | None = None) -> CodeGraphIndex:
    return CodeGraphIndex(runner=_Runner(), root_of=lambda _project_id: root, languages=languages)


def test_concurrent_callers_share_one_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path)
    real = scan_python
    builds: list[Path] = []
    gate = threading.Lock()

    def slow(path: Path) -> ExtractorGraph:
        with gate:
            builds.append(path)
        time.sleep(0.2)
        return real(path)

    monkeypatch.setattr("aconsole.core.infrastructure.codegraph.index.scan_python", slow)
    index = _index(root)

    def get_graph(_item: int) -> ExtractorGraph:
        return index.graph("p")

    with ThreadPoolExecutor(max_workers=6) as pool:
        graphs = list(pool.map(get_graph, range(6)))
    assert len(builds) == 1
    assert all(graph is graphs[0] for graph in graphs)


def test_answers_match_the_graph_and_repeat_from_the_cache(tmp_path: Path) -> None:
    index = _index(_project(tmp_path))
    graph = index.graph("p")
    node = graph.nodes[0]
    assert index.resolve("p", node.id) == node.id
    assert index.resolve("p", node.path) == node.id
    assert index.resolve("p", "no such module") is None
    first = index.dependents("p")
    assert index.dependents("p") is first
    assert index.dependents("p", frozenset({"no such kind"})) == {}


def test_language_changes_invalidate_cached_graphs_immediately(tmp_path: Path) -> None:
    enabled: list[frozenset[str]] = [frozenset({"python"})]
    index = _index(_project(tmp_path), lambda: enabled[0])
    first = index.graph("p")
    assert first.nodes
    enabled[0] = frozenset()
    assert index.graph("p").nodes == ()
    assert index.dependents("p") == {}
    enabled[0] = frozenset({"python"})
    assert index.graph("p") == first


def test_each_rebuild_is_persisted_before_publication(tmp_path: Path) -> None:

    class Store:
        def __init__(self) -> None:
            self.saved: list[tuple[str, ExtractorGraph]] = []

        def sync(self, project_id: str, graph: ExtractorGraph) -> None:
            self.saved.append((project_id, graph))

    store = Store()
    clock = [0.0]
    index = CodeGraphIndex(
        runner=_Runner(), root_of=lambda _: _project_root, store=store, clock=lambda: clock[0], ttl_s=1
    )
    _project_root = _project(tmp_path)
    first = index.graph("p")
    assert store.saved == [("p", first)]
    assert index.graph("p") is first
    assert len(store.saved) == 1
    clock[0] = 2
    _ = (tmp_path / "a.py").write_text("")
    second = index.graph("p")
    assert store.saved == [("p", first), ("p", second)]
    assert not second.edges


def test_failed_sync_is_retried_instead_of_cached(tmp_path: Path) -> None:
    import pytest

    class Store:
        calls: int = 0

        def sync(self, project_id: str, graph: ExtractorGraph) -> None:
            del project_id, graph
            self.calls += 1
            message = "database unavailable"
            raise ValueError(message)

    index = _index(_project(tmp_path))
    first = index.graph("p")
    store = Store()
    index.use_store(store)
    for _ in range(2):
        with pytest.raises(ValueError, match="database unavailable"):
            _ = index.graph("p")
    assert first.nodes
    assert store.calls == 2


class _Runner:
    def __init__(self, failed: frozenset[str] | None = None) -> None:
        self.failed: frozenset[str] = failed or frozenset()
        self.calls: list[str] = []

    def run(self, language: str, path: str, *, cwd: Path | None = None) -> ExtractorRun:
        assert path == "."
        self.calls.append(language)
        assert cwd is not None
        if language in self.failed:
            return ExtractorRun(graph=None, error=None)
        return ExtractorRun(
            graph=ExtractorGraph(
                version=1,
                nodes=(
                    GraphNode(id="a", path=f"{language}/a", name="a", kind="module"),
                    GraphNode(id="b", path=f"{language}/b", name="b", kind="module"),
                ),
                edges=(GraphEdge(source="a", target="b", kind="imports"),),
            ),
            error=None,
        )


def test_polyglot_graph_namespaces_colliding_ids_and_keeps_python_ids(tmp_path: Path) -> None:
    root = _project(tmp_path)
    for marker in ("package.json", "tsconfig.json", "Cargo.toml", "app.sln", "app.csproj"):
        _ = (root / marker).write_text("")
    runner = _Runner()
    index = CodeGraphIndex(runner=runner, root_of=lambda _: root)
    graph = index.graph("p")
    assert {node.id for node in graph.nodes} == {"a", "b", "ts:a", "ts:b", "rs:a", "rs:b", "cs:a", "cs:b"}
    assert runner.calls == ["rust-metadata", "ts", "csharp"]
    assert index.dependents("p") == {"b": ("a",), "ts:b": ("ts:a",), "rs:b": ("rs:a",), "cs:b": ("cs:a",)}
    assert index.resolve("p", "ts/a") == "ts:a"


def test_failed_typescript_keeps_python_edges_on_repeated_sync(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _ = (root / "package.json").write_text("")
    saved: list[ExtractorGraph] = []

    class Store:
        def sync(self, project_id: str, graph: ExtractorGraph) -> None:
            assert project_id == "p"
            saved.append(graph)

    runner = _Runner()
    index = CodeGraphIndex(runner=runner, root_of=lambda _: root, ttl_s=0, store=Store())
    _ = index.graph("p")
    runner.failed = frozenset({"ts"})
    graph = index.graph("p")
    assert {node.id for node in graph.nodes} == {"a", "b"}
    assert graph.edges == (GraphEdge(source="a", target="b", kind="imports"),)
    assert all({"a", "b"} <= {node.id for node in snapshot.nodes} for snapshot in saved)
    assert len(saved) == 2


def test_failure_does_not_stop_later_languages_and_all_failed_raises(tmp_path: Path) -> None:
    import pytest

    from aconsole.core.domain.errors import DomainError

    _ = (tmp_path / "package.json").write_text("")
    _ = (tmp_path / "Cargo.toml").write_text("")
    runner = _Runner(frozenset({"rust-metadata"}))
    index = CodeGraphIndex(runner=runner, root_of=lambda _: tmp_path, ttl_s=0)
    assert {node.id for node in index.graph("p").nodes} == {"ts:a", "ts:b"}
    runner.failed = frozenset({"rust-metadata", "ts"})
    with pytest.raises(DomainError, match="CODEGRAPH_EXTRACTOR_FAILED"):
        _ = index.graph("p")


def test_csharp_markers_are_root_files_and_disabled_languages_are_not_run(tmp_path: Path) -> None:
    runner = _Runner()
    (tmp_path / "nested").mkdir()
    _ = (tmp_path / "nested/app.csproj").write_text("")
    (tmp_path / "directory.sln").mkdir()
    index = CodeGraphIndex(runner=runner, root_of=lambda _: tmp_path, ttl_s=0)
    assert index.graph("p").nodes == ()
    assert not runner.calls
    for marker in ("app.sln", "app.csproj"):
        path = tmp_path / marker
        _ = path.write_text("")
        assert {node.id for node in index.graph("p").nodes} == {"cs:a", "cs:b"}
        path.unlink()
    index = CodeGraphIndex(runner=runner, root_of=lambda _: tmp_path, languages=lambda: frozenset({"python"}))
    _ = (tmp_path / "package.json").write_text("")
    assert index.graph("p").nodes == ()
    assert runner.calls == ["csharp", "csharp"]


def test_existing_python_fact_and_import_survive_polyglot_resync(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from sqlalchemy import select

    from aconsole.core.application.ports.knowledge_store import StoredFact
    from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
    from aconsole.core.infrastructure.persistence.models import KbEdge, KbEdgeKind, Project
    from aconsole.core.infrastructure.persistence.repositories.code_graph import CodeGraphDatabase
    from aconsole.core.infrastructure.persistence.repositories.knowledge import KnowledgeDatabase, code_ref_id
    from aconsole.core.infrastructure.persistence.writer import Writer

    root = tmp_path / "repo"
    root.mkdir()
    _ = _project(root)
    url = f"sqlite:///{tmp_path / 'state.db'}"
    migrate(url, "head")
    writer = Writer(write_engine(url))
    writer.start()
    now = datetime.now(UTC)
    try:
        _ = writer.call(
            lambda session: session.add(Project(id="p", name="P", root_path=str(root), languages=[], created_at=now))
        )
        knowledge = KnowledgeDatabase(writer)
        knowledge.save_fact(
            StoredFact(
                id="kb-1",
                project_id="p",
                kind="fact",
                title="Uses a",
                body="",
                source="test",
                code_refs=("a.py",),
                stale=False,
                note_ref="test-note",
                updated_at=now,
            ),
            None,
        )
        runner = _Runner()
        index = CodeGraphIndex(runner=runner, root_of=lambda _: root, ttl_s=0, store=CodeGraphDatabase(writer))
        original = index.graph("p")
        _ = (root / "package.json").write_text("")
        _ = index.graph("p")
        runner.failed = frozenset({"ts"})
        assert index.graph("p") == original
        rows: list[tuple[str, str, KbEdgeKind]] = []
        _ = writer.call(
            lambda session: rows.extend(
                (row[0], row[1], row[2]) for row in session.execute(select(KbEdge.src_id, KbEdge.dst_id, KbEdge.kind))
            )
        )
        assert ("kb-1", code_ref_id("p", "a.py"), KbEdgeKind.CODE_REF) in rows
        assert (code_ref_id("p", "a.py"), code_ref_id("p", "b.py"), KbEdgeKind.IMPORTS) in rows
        assert knowledge.load()[0][0].code_refs == ("a.py",)
    finally:
        writer.stop()
