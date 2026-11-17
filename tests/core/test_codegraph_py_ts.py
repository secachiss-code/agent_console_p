"""Python and TypeScript fixture graphs match the edges those files import."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import msgspec
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


@runtime_checkable
class _AstExtractor(Protocol):
    def _ast_graph(self, root: Path) -> dict[str, object]: ...


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
    assert isinstance(module, _AstExtractor)
    raw = json.dumps(module._ast_graph(_PYTHON), sort_keys=True).encode()  # pyright: ignore[reportPrivateUsage]
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


def test_cruiser_keeps_dynamic_imports_and_deduplicates_local_edges() -> None:
    document = {
        "modules": [
            {
                "source": "src/main.ts",
                "dependencies": [
                    {"resolved": "src/lazy.ts", "dynamic": True},
                    {"resolved": "src/lazy.ts", "dynamic": False},
                    {"resolved": "node_modules/pkg/index.js"},
                    {"resolved": "../outside.ts"},
                ],
            },
            {"source": "src/lazy.ts"},
            {"source": "src/lazy.ts"},
            {"source": "node_modules/pkg/index.js"},
            {"source": "../outside.ts"},
        ],
    }
    graph = from_cruiser(json.dumps(document).encode())
    assert _edges(graph) == frozenset({("src/main.ts", "src/lazy.ts")})
    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1
    assert graph.edges[0].kind == "imports"


def _cruiser_stub(root: Path, *, version: str = "18.7.0", fail: bool = False) -> Path:
    script = root / "cruiser.py"
    document = {
        "modules": [
            {"source": "src/main.ts", "dependencies": [{"resolved": "src/lazy.ts", "dynamic": True}]},
            {"source": "src/lazy.ts"},
        ]
    }
    _ = script.write_text(
        f"""import json, pathlib, sys
if sys.argv[1:] == ['--version']:
    print({version!r})
    sys.exit(0)
pathlib.Path('argv.json').write_text(json.dumps(sys.argv[1:]))
if {fail!r}:
    print('failure', file=sys.stderr)
    sys.exit(17)
print({json.dumps(document)!r})
"""
    )
    return script


def test_external_ts_adapter_uses_preinstalled_version_and_project_tsconfig(tmp_path: Path) -> None:
    from aconsole.core.infrastructure.codegraph.typescript import extract_typescript

    script = _cruiser_stub(tmp_path)
    _ = (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{"baseUrl":"."}}')
    graph = extract_typescript(Path(sys.executable), script, ".", root=tmp_path)
    assert _edges(graph) == frozenset({("src/main.ts", "src/lazy.ts")})
    args = msgspec.json.decode((tmp_path / "argv.json").read_bytes(), type=tuple[str, ...])
    assert "--no-config" in args
    assert "--ts-config" in args
    assert str(tmp_path / "tsconfig.json") in args
    assert "--exclude" in args
    assert not ({"npm", "npx", "install"} & set(args))


def test_external_ts_adapter_refuses_missing_and_wrong_version_tools(tmp_path: Path) -> None:
    from aconsole.core.infrastructure.codegraph.typescript import extract_typescript

    with pytest.raises(ValueError, match="unavailable"):
        _ = extract_typescript(Path(sys.executable), tmp_path / "missing", ".", root=tmp_path)
    script = _cruiser_stub(tmp_path, version="18.8.0")
    with pytest.raises(ValueError, match=r"18\.7\.0"):
        _ = extract_typescript(Path(sys.executable), script, ".", root=tmp_path)
    assert not (tmp_path / "argv.json").exists()


def _hostrun_adapter(root: Path, node: Path, cruiser: Path) -> Path:
    hostrun = root / "hostrun"
    _ = hostrun.write_text(
        f"""#!{sys.executable}
import os, sys
os.chdir(os.environ['HOSTRUN_CWD'])
os.execv({sys.executable!r}, [{sys.executable!r}, '-m',
    'aconsole.core.infrastructure.codegraph.typescript', '--node', {str(node)!r},
    '--cruiser', {str(cruiser)!r}, sys.argv[3]])
"""
    )
    hostrun.chmod(0o755)
    return hostrun


@pytest.mark.parametrize("mode", ["failed", "missing"])
def test_ts_cli_failure_reaches_runner_as_extractor_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    from aconsole.core.infrastructure.codegraph.runner import ExtractorRunner

    script = _cruiser_stub(tmp_path, fail=True) if mode == "failed" else tmp_path / "missing"
    hostrun = _hostrun_adapter(tmp_path, Path(sys.executable), script)
    monkeypatch.setenv("PYTHONPATH", str(_ROOT / "src"))
    result = ExtractorRunner(hostrun).run("ts", ".", cwd=tmp_path)
    assert result.graph is None
    assert result.error is not None
    assert result.error.message == "CODEGRAPH_EXTRACTOR_FAILED"
    assert (tmp_path / "argv.json").is_file() == (mode == "failed")


def test_installed_cruiser_extracts_dynamic_import_and_tsconfig_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aconsole.core.infrastructure.codegraph.runner import ExtractorRunner
    from aconsole.core.infrastructure.codegraph.typescript import extract_typescript

    image_home = next(
        (
            parent / ".containers/containers/devtools"
            for parent in _ROOT.parents
            if (parent / ".containers/containers/devtools").is_dir()
        ),
        None,
    )
    if image_home is None:
        pytest.skip("real extraction needs the devtools image")
    node = image_home / ".local/share/fnm/node-versions/v24.22.0/installation/bin/node"
    cruiser = (
        image_home
        / ".local/lib/aconsole/dependency-cruiser-18.7.0"
        / "node_modules/dependency-cruiser/bin/dependency-cruiser.mjs"
    )
    assert node.is_file(), "devtools must provide Node"
    assert cruiser.is_file(), "devtools must provide dependency-cruiser 18.7.0"
    (tmp_path / "src").mkdir()
    _ = (tmp_path / "src/main.ts").write_text('export const load = () => import("@app/lazy");\n')
    _ = (tmp_path / "src/lazy.ts").write_text("export const value: number = 7;\n")
    _ = (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{"baseUrl":".","paths":{"@app/*":["src/*"]}}}')
    # A project-controlled executable config must not run during extraction.
    _ = (tmp_path / ".dependency-cruiser.cjs").write_text('throw new Error("config must not execute");\n')
    (tmp_path / "node_modules/pkg").mkdir(parents=True)
    _ = (tmp_path / "node_modules/pkg/broken.ts").write_text("this is not valid TypeScript {{")
    graph = extract_typescript(node, cruiser, "src", root=tmp_path)
    assert _edges(graph) == frozenset({("src/main.ts", "src/lazy.ts")})
    assert {n.path for n in graph.nodes} == {"src/main.ts", "src/lazy.ts"}
    assert graph == extract_typescript(node, cruiser, "src", root=tmp_path)
    hostrun = _hostrun_adapter(tmp_path, node, cruiser)
    monkeypatch.setenv("PYTHONPATH", str(_ROOT / "src"))
    run = ExtractorRunner(hostrun).run("ts", "src", cwd=tmp_path)
    assert run.error is None
    assert run.graph == graph
