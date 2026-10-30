"""Python and TypeScript fixture graphs match the edges those files import."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph, decode_graph
from aconsole.core.infrastructure.codegraph.python import build_python_graph
from aconsole.core.infrastructure.codegraph.typescript import from_cruiser

if TYPE_CHECKING:
    from types import ModuleType

_ROOT = Path(__file__).resolve().parents[2]
_PYTHON = _ROOT / "tests" / "fixtures" / "codegraph" / "python"
_TS = _ROOT / "tests" / "fixtures" / "codegraph" / "ts"
_SCRIPT = _ROOT / "tools" / "codegraph" / "extract_python.py"
_PYTHON_EDGES = frozenset({("graphsample.app", "graphsample.util"), ("graphsample.util", "graphsample.leaf")})
_TS_EDGES = frozenset({("src/main.ts", "src/util.ts"), ("src/util.ts", "src/leaf.ts")})


def _edges(graph: ExtractorGraph) -> frozenset[tuple[str, str]]:
    return frozenset((edge.source, edge.target) for edge in graph.edges)


def test_codegraph_py_ts_python_edges_match_the_fixture() -> None:
    graph = build_python_graph(_PYTHON)
    assert _edges(graph) == _PYTHON_EDGES
    assert {node.id for node in graph.nodes} == {
        "graphsample",
        "graphsample.app",
        "graphsample.leaf",
        "graphsample.util",
    }
    assert "os" not in {node.id for node in graph.nodes}
    kinds = {node.id: node.kind for node in graph.nodes}
    assert kinds["graphsample"] == "package"
    assert kinds["graphsample.app"] == "module"


def test_codegraph_py_ts_typescript_edges_match_the_fixture() -> None:
    graph = from_cruiser((_TS / "cruiser.json").read_bytes())
    assert _edges(graph) == _TS_EDGES
    assert {node.path for node in graph.nodes} == {"src/leaf.ts", "src/main.ts", "src/util.ts"}
    assert "fs" not in {edge.target for edge in graph.edges}


def test_codegraph_py_ts_extractor_script_matches_grimp() -> None:
    completed = subprocess.run(
        [sys.executable, str(_SCRIPT), str(_PYTHON)],
        check=False,
        capture_output=True,
        cwd=_ROOT,
    )
    assert completed.returncode == 0
    parsed = decode_graph(completed.stdout)
    assert _edges(parsed) == _edges(build_python_graph(_PYTHON))


def test_codegraph_py_ts_ast_fallback_matches_the_fixture() -> None:
    """The devtools image has no grimp. The ast walk must keep the same edges."""
    module = _extractor()
    raw = json.dumps(module._ast_graph(_PYTHON), sort_keys=True).encode()
    assert _edges(decode_graph(raw)) == _PYTHON_EDGES


def _extractor() -> ModuleType:
    spec = importlib.util.spec_from_file_location("extract_python_under_test", _SCRIPT)
    if spec is None or spec.loader is None:
        message = "extractor script did not load"
        raise RuntimeError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_codegraph_py_ts_cruiser_rejects_a_list() -> None:
    with pytest.raises(ValueError, match="schema"):
        _ = from_cruiser(b"[]")
