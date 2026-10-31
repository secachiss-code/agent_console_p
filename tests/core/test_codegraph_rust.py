"""Cargo metadata and documented cargo-modules DOT become sorted protocol graphs."""

from __future__ import annotations

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
    raw = br"""digraph {
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
    output.write_bytes((_RUST / "metadata.json").read_bytes())
    hostrun = tmp_path / "hostrun"
    hostrun.write_text('#!/bin/sh\ncat "$ACONSOLE_EXTRACTOR_STDOUT"\n')
    hostrun.chmod(0o755)
    monkeypatch.setenv("ACONSOLE_EXTRACTOR_STDOUT", str(output))

    result = ExtractorRunner(hostrun).run("rust-metadata", "tests/fixtures/codegraph/rust/Cargo.toml")

    assert result.error is None
    assert result.graph == from_cargo_metadata(output.read_bytes())
