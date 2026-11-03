"""Asking for one project's graph at once builds it once, and later questions do not walk it again."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from aconsole.core.infrastructure.codegraph import index as index_module
from aconsole.core.infrastructure.codegraph.index import CodeGraphIndex

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "a.py").write_text("import b\n")
    (tmp_path / "b.py").write_text("")
    return tmp_path


def _index(root: Path) -> CodeGraphIndex:
    return CodeGraphIndex(runner=object(), root_of=lambda _project_id: root)  # pyright: ignore[reportArgumentType]


def test_concurrent_callers_share_one_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path)
    real = index_module.scan_python
    builds: list[Path] = []
    gate = threading.Lock()

    def slow(path: Path) -> object:
        with gate:
            builds.append(path)
        time.sleep(0.2)
        return real(path)

    monkeypatch.setattr(index_module, "scan_python", slow)
    index = _index(root)
    with ThreadPoolExecutor(max_workers=6) as pool:
        graphs = list(pool.map(lambda _item: index.graph("p"), range(6)))
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
