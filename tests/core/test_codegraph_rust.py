"""Cargo metadata and documented cargo-modules DOT become sorted protocol graphs."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph, GraphEdge, GraphNode
from aconsole.core.infrastructure.codegraph.runner import ExtractorRunner
from aconsole.core.infrastructure.codegraph.rust import from_cargo_metadata, from_cargo_modules_dot

_ROOT = Path(__file__).resolve().parents[2]
_RUST = _ROOT / "tests" / "fixtures" / "codegraph" / "rust"
_CORE_ID = "crates/console-core/Cargo.toml"
_UTIL_ID = "crates/console-utils/Cargo.toml"


def test_codegraph_rust_cargo_metadata_contains_known_packages_and_dependencies() -> None:
    graph = from_cargo_metadata((_RUST / "metadata.json").read_bytes())

    assert graph == ExtractorGraph(
        version=1,
        nodes=(
            GraphNode(
                id=_CORE_ID,
                path="crates/console-core/Cargo.toml",
                name="console-core",
                kind="package",
            ),
            GraphNode(
                id=_UTIL_ID,
                path="crates/console-utils/Cargo.toml",
                name="console-utils",
                kind="package",
            ),
        ),
        edges=(GraphEdge(source=_CORE_ID, target=_UTIL_ID, kind="depends_on"),),
    )


def test_codegraph_rust_cargo_metadata_is_deterministic_with_closed_edges() -> None:
    raw = (_RUST / "metadata.json").read_bytes()
    assert from_cargo_metadata(raw) == from_cargo_metadata(raw)
    graph = from_cargo_metadata(raw)
    assert all(edge.source in {node.id for node in graph.nodes} for edge in graph.edges)
    assert all(edge.target in {node.id for node in graph.nodes} for edge in graph.edges)
    assert tuple(sorted(graph.nodes, key=lambda node: node.id)) == graph.nodes
    assert tuple(sorted(graph.edges, key=lambda edge: (edge.source, edge.target))) == graph.edges


def test_codegraph_rust_metadata_ids_do_not_depend_on_workspace_root() -> None:
    raw = (_RUST / "metadata.json").read_bytes()
    moved = raw.replace(b"/workspace", b"/tmp/moved-workspace")

    assert from_cargo_metadata(moved) == from_cargo_metadata(raw)


def test_codegraph_rust_metadata_skips_unresolved_registry_dependency() -> None:
    graph = from_cargo_metadata((_RUST / "metadata_external_synthetic.json").read_bytes())

    assert graph.nodes == (
        GraphNode(
            id="crates/console-core/Cargo.toml",
            path="crates/console-core/Cargo.toml",
            name="console-core",
            kind="package",
        ),
    )
    assert graph.edges == ()


def test_codegraph_rust_metadata_rejects_malformed_documents() -> None:
    for raw in (b"", b"not json", b"[]", b'{"packages": "not an array"}'):
        with pytest.raises(ValueError, match="Cargo metadata does not match the schema"):
            _ = from_cargo_metadata(raw)


def test_codegraph_rust_cargo_modules_dot_matches_fully_qualified_module_ids() -> None:
    graph = from_cargo_modules_dot((_RUST / "modules.dot").read_bytes())

    assert graph == ExtractorGraph(
        version=1,
        nodes=(
            GraphNode(id="console_core", path="console_core", name="console_core", kind="module"),
            GraphNode(
                id="console_core::approvals",
                path="console_core::approvals",
                name="approvals",
                kind="module",
            ),
            GraphNode(
                id="console_core::tabs",
                path="console_core::tabs",
                name="tabs",
                kind="module",
            ),
        ),
        edges=(
            GraphEdge(source="console_core", target="console_core::approvals", kind="owns"),
            GraphEdge(source="console_core", target="console_core::tabs", kind="owns"),
        ),
    )


def test_codegraph_rust_cargo_modules_dot_parses_renderer_attributes_and_relationships() -> None:
    graph = from_cargo_modules_dot((_RUST / "modules_renderer_subset.dot").read_bytes())

    assert graph == ExtractorGraph(
        version=1,
        nodes=(
            GraphNode(
                id="readme_graph_example",
                path="readme_graph_example",
                name="readme_graph_example",
                kind="module",
            ),
            GraphNode(
                id="readme_graph_example::methods",
                path="readme_graph_example::methods",
                name="methods",
                kind="module",
            ),
            GraphNode(
                id="readme_graph_example::uses",
                path="readme_graph_example::uses",
                name="uses",
                kind="module",
            ),
        ),
        edges=(
            GraphEdge(
                source="readme_graph_example",
                target="readme_graph_example::methods",
                kind="owns",
            ),
            GraphEdge(source="readme_graph_example", target="readme_graph_example::uses", kind="owns"),
        ),
    )


def test_codegraph_rust_cargo_modules_dot_preserves_uses_edge_labels() -> None:
    graph = from_cargo_modules_dot((_RUST / "modules_renderer_uses.dot").read_bytes())

    assert graph.edges == (
        GraphEdge(
            source="readme_graph_example::derives::Dummy::clone",
            target="bool",
            kind="uses",
        ),
        GraphEdge(
            source="readme_graph_example::derives::Dummy::clone",
            target="readme_graph_example::derives::Dummy",
            kind="uses",
        ),
    )


def test_codegraph_rust_cargo_modules_dot_sorts_and_deduplicates_edges() -> None:
    raw = (_RUST / "modules.dot").read_bytes()
    graph = from_cargo_modules_dot(raw)
    assert graph == from_cargo_modules_dot(raw)
    assert tuple(sorted(graph.nodes, key=lambda node: node.id)) == graph.nodes
    assert tuple(sorted(graph.edges, key=lambda edge: (edge.source, edge.target))) == graph.edges
    assert len(graph.edges) == len({(edge.source, edge.target) for edge in graph.edges})


def test_codegraph_rust_cargo_modules_dot_rejects_malformed_documents() -> None:
    for raw in (
        b"",
        b"digraph {",
        b"not dot",
        b'digraph { "console_core" -> "missing"; }',
    ):
        with pytest.raises(ValueError, match="unsupported or malformed cargo-modules DOT"):
            _ = from_cargo_modules_dot(raw)


def test_codegraph_rust_cargo_modules_dot_decodes_escaped_identifier_characters() -> None:
    raw = rb"""digraph {
"crate::quoted\"module";
"crate::path\\module";
"crate::quoted\"module" -> "crate::path\\module" [label="owns"];
}"""

    assert from_cargo_modules_dot(raw) == ExtractorGraph(
        version=1,
        nodes=(
            GraphNode(
                id="crate::path\\module",
                path="crate::path\\module",
                name="path\\module",
                kind="module",
            ),
            GraphNode(
                id='crate::quoted"module',
                path='crate::quoted"module',
                name='quoted"module',
                kind="module",
            ),
        ),
        edges=(
            GraphEdge(
                source='crate::quoted"module',
                target="crate::path\\module",
                kind="owns",
            ),
        ),
    )


def test_codegraph_rust_runner_dispatches_cargo_metadata_to_rust_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "metadata.json"
    _ = output.write_bytes((_RUST / "metadata.json").read_bytes())
    hostrun = tmp_path / "hostrun"
    _ = hostrun.write_text('#!/bin/sh\ncat "$ACONSOLE_EXTRACTOR_STDOUT"\n')
    hostrun.chmod(0o755)
    monkeypatch.setenv("ACONSOLE_EXTRACTOR_STDOUT", str(output))

    result = ExtractorRunner(hostrun).run("rust-metadata", "tests/fixtures/codegraph/rust/Cargo.toml")

    assert result.error is None
    assert result.graph == from_cargo_metadata(output.read_bytes())


@pytest.fixture(scope="module")
def rsgraph_binary() -> Path:
    """Build the external tool offline in devtools; never build the project fixture."""
    import os
    import shutil

    rustup_home = Path(os.environ.get("RUSTUP_HOME", str(Path.home() / ".rustup")))
    if not rustup_home.is_dir():
        rustup_home = next(
            (
                parent / ".containers/containers/devtools/.rustup"
                for parent in _ROOT.parents
                if (parent / ".containers/containers/devtools/.rustup").is_dir()
            ),
            rustup_home,
        )
    toolchain = rustup_home / "toolchains/stable-x86_64-unknown-linux-gnu/bin"
    env = os.environ.copy()
    if toolchain.is_dir():
        env["PATH"] = f"{toolchain}:{env.get('PATH', '')}"
        _ = env.setdefault("CARGO_HOME", str(rustup_home.parent / ".cargo"))
    cargo = shutil.which("cargo", path=env.get("PATH"))
    if cargo is None:
        pytest.skip("Rust toolchain is required for the real syn integration fixture")
    _ = subprocess.run(
        [cargo, "build", "--offline", "--locked", "--manifest-path", str(_ROOT / "tools/rsgraph/Cargo.toml")],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    return _ROOT / "tools/rsgraph/target/debug/aconsole-rsgraph"


def _run_rust(binary: Path, root: Path) -> subprocess.CompletedProcess[str]:
    import json

    packages = [
        {
            "id": name,
            "name": name,
            "manifest_path": str(root / name / "Cargo.toml"),
            "dependencies": [{"name": "utils", "path": str(root / "utils")}] if name == "demo" else [],
            "targets": [{"name": name, "src_path": str(root / name / "src/lib.rs")}],
        }
        for name in ("demo", "utils")
    ]
    metadata = json.dumps({"workspace_root": str(root), "workspace_members": ["demo", "utils"], "packages": packages})
    return subprocess.run([str(binary)], input=metadata, text=True, capture_output=True, check=False, timeout=10)


def _extract_rust(binary: Path, root: Path) -> ExtractorGraph:
    from aconsole.core.infrastructure.codegraph.protocol import decode_graph

    result = _run_rust(binary, root)
    result.check_returncode()
    graph = decode_graph(result.stdout.encode())
    assert from_cargo_metadata(result.stdout.encode()) == graph
    return graph


def _rust_workspace(root: Path) -> None:
    for name in ("demo", "utils"):
        directory = root / name
        (directory / "src").mkdir(parents=True)
        _ = (directory / "Cargo.toml").write_text(f'[package]\nname = "{name}"\nversion = "0.1.0"\n')
        _ = (directory / "src/lib.rs").write_text("pub fn value() -> u32 { 1 }\n")
    _ = (root / "demo/src/lib.rs").write_text(
        "mod api; mod worker;\npub mod inline { use crate::api::Value; }\nuse unknown_external::Thing;\n"
    )
    _ = (root / "demo/src/api.rs").write_text("pub struct Value;\npub fn value() -> u32 { 1 }\n")
    _ = (root / "demo/src/worker.rs").write_text(
        "use crate::api::{Value as Renamed, value};\nuse super::api::*;\nmod nested { use super::super::api::Value; }\n"
    )


def test_syn_extracts_closed_deterministic_module_graph_and_keeps_crate_edges(
    tmp_path: Path,
    rsgraph_binary: Path,
) -> None:
    from aconsole.core.infrastructure.codegraph.protocol import prefix_graph

    _rust_workspace(tmp_path)
    graph = _extract_rust(rsgraph_binary, tmp_path)
    assert graph == _extract_rust(rsgraph_binary, tmp_path)
    assert prefix_graph(graph, "rs:") == graph
    by_name = {node.name: node.id for node in graph.nodes if node.kind == "module"}
    edges = {(edge.source, edge.target, edge.kind) for edge in graph.edges}
    assert ("rs-crate:demo/Cargo.toml", "rs-crate:utils/Cargo.toml", "depends_on") in edges
    assert (by_name["demo"], by_name["api"], "imports") in edges
    assert (by_name["worker"], by_name["api"], "imports") in edges
    assert (by_name["nested"], by_name["api"], "imports") in edges
    assert (by_name["inline"], by_name["api"], "imports") in edges
    assert all(node.id.startswith(("rs:", "rs-crate:")) for node in graph.nodes)
    assert all(not Path(node.path).is_absolute() for node in graph.nodes)
    assert all(
        edge.source in {node.id for node in graph.nodes} and edge.target in {node.id for node in graph.nodes}
        for edge in graph.edges
    )
    assert not any("unknown_external" in edge.target for edge in graph.edges)
    assert len(graph.edges) == len(edges)
    assert graph.nodes == tuple(sorted(graph.nodes, key=lambda node: node.id))


def test_syn_interface_ignores_function_bodies_and_changes_with_signatures(
    tmp_path: Path,
    rsgraph_binary: Path,
) -> None:
    _rust_workspace(tmp_path)
    before = _extract_rust(rsgraph_binary, tmp_path)
    path = tmp_path / "demo/src/api.rs"
    _ = path.write_text("pub struct Value;\npub fn value() -> u32 { 987 }\n")
    assert _extract_rust(rsgraph_binary, tmp_path) == before
    _ = path.write_text("pub struct Value;\npub fn value(arg: u32) -> u32 { arg }\n")
    assert _extract_rust(rsgraph_binary, tmp_path) != before


def test_syn_ids_survive_workspace_move(tmp_path: Path, rsgraph_binary: Path) -> None:
    _rust_workspace(tmp_path / "one")
    _rust_workspace(tmp_path / "two")
    assert _extract_rust(rsgraph_binary, tmp_path / "one") == _extract_rust(rsgraph_binary, tmp_path / "two")


def test_real_syn_output_reaches_polyglot_index(tmp_path: Path, rsgraph_binary: Path) -> None:
    import msgspec

    from aconsole.core.infrastructure.codegraph.index import CodeGraphIndex

    _rust_workspace(tmp_path)
    graph = _extract_rust(rsgraph_binary, tmp_path)
    _ = (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["demo", "utils"]\n')
    output = tmp_path / "graph.json"
    _ = output.write_bytes(msgspec.json.encode(graph))
    hostrun = tmp_path / "hostrun"
    _ = hostrun.write_text(f'#!/bin/sh\ncat "{output}"\n')
    hostrun.chmod(0o755)
    index = CodeGraphIndex(ExtractorRunner(hostrun), lambda _project: tmp_path)
    assert index.graph("rust-project") == graph


@pytest.mark.parametrize("bad_source", ["pub fn broken( {", '#[path = "lib.rs"] mod recursive;'])
def test_syn_rejects_invalid_or_recursive_modules_without_partial_stdout(
    tmp_path: Path,
    rsgraph_binary: Path,
    bad_source: str,
) -> None:

    _rust_workspace(tmp_path)
    _ = (tmp_path / "demo/src/lib.rs").write_text(bad_source)
    result = _run_rust(rsgraph_binary, tmp_path)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "rsgraph:" in result.stderr
