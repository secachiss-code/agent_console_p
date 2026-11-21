"""Interface hashes avoid edge writes without losing facts or invalid-file cleanup."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from sqlalchemy.event import listen

from aconsole.core.application.ports.knowledge_store import StoredFact
from aconsole.core.application.ports.plan_store import StoreWriteError
from aconsole.core.infrastructure.codegraph.local_python import scan_python
from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph, GraphEdge, GraphNode
from aconsole.core.infrastructure.codegraph.skeleton import file_hashes, python_skeleton
from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from aconsole.core.infrastructure.persistence.models import KbEdge, KbEdgeKind, KbNode, Project
from aconsole.core.infrastructure.persistence.repositories.code_graph import CodeGraphDatabase
from aconsole.core.infrastructure.persistence.repositories.knowledge import KnowledgeDatabase, code_ref_id
from aconsole.core.infrastructure.persistence.writer import Writer

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


@pytest.fixture
def stored(tmp_path: Path) -> Iterator[tuple[Writer, Engine, Path]]:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    _ = (root / "src/x.py").write_text("def x(value: int) -> int:\n    return value\n")
    _ = (root / "src/y.py").write_text("")
    url = f"sqlite:///{tmp_path / 'state.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    writer = Writer(engine)
    writer.start()
    _ = writer.call(
        lambda session: session.add(
            Project(id="p", name="P", root_path=str(root), languages=[], created_at=datetime.now(UTC))
        )
    )
    try:
        yield writer, engine, root
    finally:
        writer.stop()
        engine.dispose()


def _graph(*, linked: bool = True) -> ExtractorGraph:
    return ExtractorGraph(
        version=1,
        nodes=(
            GraphNode(id="x", path="src/x.py", name="x", kind="module"),
            GraphNode(id="y", path="src/y.py", name="y", kind="module"),
        ),
        edges=(GraphEdge(source="x", target="y", kind="imports"),) if linked else (),
    )


def _snapshot(
    writer: Writer,
) -> tuple[dict[str, tuple[str | None, str | None, str | None]], set[tuple[str, str, KbEdgeKind]]]:
    nodes: dict[str, tuple[str | None, str | None, str | None]] = {}
    edges: set[tuple[str, str, KbEdgeKind]] = set()

    def read(session: Session) -> None:
        nodes.update(
            (node.id, (node.language, node.body_hash, node.interface_hash)) for node in session.scalars(select(KbNode))
        )
        edges.update((edge.src_id, edge.dst_id, edge.kind) for edge in session.scalars(select(KbEdge)))

    _ = writer.call(read)
    return nodes, edges


def _capture(engine: Engine) -> list[str]:
    writes: list[str] = []

    def record(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        if statement.startswith(("INSERT", "DELETE", "UPDATE")):
            writes.append(statement)

    listen(engine, "before_cursor_execute", record)
    return writes


def _fact(writer: Writer) -> KnowledgeDatabase:
    knowledge = KnowledgeDatabase(writer)
    knowledge.save_fact(
        StoredFact(
            id="kb-1",
            project_id="p",
            kind="fact",
            title="Uses x",
            body="",
            source="test",
            code_refs=("src/x.py",),
            stale=False,
            note_ref="test-note",
            updated_at=datetime.now(UTC),
        ),
        None,
    )
    return knowledge


def test_incremental_graph_shares_fact_nodes_and_preserves_fact_edges(stored: tuple[Writer, Engine, Path]) -> None:
    writer, engine, root = stored
    knowledge = _fact(writer)
    database = CodeGraphDatabase(writer)
    database.sync("p", _graph())
    writes = _capture(engine)
    database.sync("p", _graph())
    assert writes == []
    _ = (root / "src/x.py").write_text("def x(value: int, other: int) -> int:\n    return value + other\n")
    database.sync("p", _graph(linked=False))
    nodes, edges = _snapshot(writer)
    assert len(nodes) == 3
    assert nodes["kb-1"] == (None, None, None)
    assert edges == {("kb-1", code_ref_id("p", "src/x.py"), KbEdgeKind.CODE_REF)}
    assert knowledge.load()[0][0].code_refs == ("src/x.py",)


def test_body_edit_with_same_interface_writes_no_edges(stored: tuple[Writer, Engine, Path]) -> None:
    writer, engine, root = stored
    _ = _fact(writer)
    database = CodeGraphDatabase(writer)
    database.sync("p", _graph())
    before, edges = _snapshot(writer)
    writes = _capture(engine)
    _ = (root / "src/x.py").write_text("def x(value: int) -> int:\n    return value + 5\n")
    database.sync("p", _graph())
    after, actual = _snapshot(writer)
    identifier = code_ref_id("p", "src/x.py")
    assert after[identifier][0] == "python"
    assert after[identifier][1] != before[identifier][1]
    assert after[identifier][2] == before[identifier][2]
    assert actual == edges
    assert writes
    assert all("kb_edges" not in statement for statement in writes)
    assert after[identifier][1] == hashlib.sha256((root / "src/x.py").read_bytes()).hexdigest()
    assert after[identifier][2] == hashlib.sha256(python_skeleton((root / "src/x.py").read_text()).encode()).hexdigest()


def test_import_edit_updates_impact_with_same_interface(stored: tuple[Writer, Engine, Path]) -> None:
    writer, _engine, root = stored
    source = root / "src/x.py"
    declaration = source.read_text()
    _ = (root / "src/z.py").write_text("")
    _ = source.write_text("import y\n" + declaration)
    database = CodeGraphDatabase(writer)
    database.sync("p", scan_python(root))
    x, y, z = (code_ref_id("p", f"src/{name}.py") for name in ("x", "y", "z"))
    before, _ = _snapshot(writer)
    assert database.impact("p", y, 1, 5) == ([(x, 1)], False)
    assert database.impact("p", z, 1, 5) == ([], False)

    _ = source.write_text("import z\n" + declaration)
    database.sync("p", scan_python(root))

    after, _ = _snapshot(writer)
    assert after[x][1] != before[x][1]
    assert after[x][2] == before[x][2]
    assert database.impact("p", y, 1, 5) == ([], False)
    assert database.impact("p", z, 1, 5) == ([(x, 1)], False)


def test_syntax_error_clears_edges_even_with_stale_extractor_edges(stored: tuple[Writer, Engine, Path]) -> None:
    writer, _engine, root = stored
    database = CodeGraphDatabase(writer)
    database.sync("p", _graph())
    _ = (root / "src/x.py").write_text("def (\n")
    database.sync("p", _graph())
    nodes, edges = _snapshot(writer)
    assert nodes[code_ref_id("p", "src/x.py")][2] == ""
    assert not edges
    database.sync("p", _graph())
    assert not _snapshot(writer)[1]
    _ = (root / "src/x.py").write_text("def x(value: int) -> int:\n    return value\n")
    database.sync("p", _graph())
    assert _snapshot(writer)[1]


def test_vanished_modules_drop_orphans_and_retain_fact_identity(stored: tuple[Writer, Engine, Path]) -> None:
    writer, _engine, _root = stored
    knowledge = _fact(writer)
    database = CodeGraphDatabase(writer)
    database.sync("p", _graph())
    database.sync("p", ExtractorGraph(version=1, nodes=(), edges=()))
    nodes, edges = _snapshot(writer)
    assert code_ref_id("p", "src/y.py") not in nodes
    assert nodes[code_ref_id("p", "src/x.py")] == (None, None, None)
    assert edges == {("kb-1", code_ref_id("p", "src/x.py"), KbEdgeKind.CODE_REF)}
    database.sync("p", _graph())
    assert knowledge.load()[0][0].code_refs == ("src/x.py",)
    assert len(_snapshot(writer)[0]) == 3


def test_foreign_symbols_and_implements_preserve_edges_on_body_edits(stored: tuple[Writer, Engine, Path]) -> None:
    writer, engine, root = stored
    _ = (root / "src/x.cs").write_text("class X : I { int f() => 1; }")
    _ = (root / "src/y.cs").write_text("interface I {}")
    x = GraphNode(id="cs:x", path="src/x.cs", name="X", kind="module", symbols=("class X : I", "int f()"))
    y = GraphNode(id="cs:y", path="src/y.cs", name="I", kind="module", symbols=("interface I",))
    edge = GraphEdge(source=x.id, target=y.id, kind="implements")
    graph = ExtractorGraph(version=1, nodes=(x, y), edges=(edge,))
    database = CodeGraphDatabase(writer)
    database.sync("p", graph)
    writes = _capture(engine)
    _ = (root / "src/x.cs").write_text("class X : I { int f() => 2; }")
    # Declaration order has no effect on the canonical hash.
    reversed_x = GraphNode(id=x.id, path=x.path, name=x.name, kind=x.kind, symbols=tuple(reversed(x.symbols)))
    database.sync("p", ExtractorGraph(version=1, nodes=(reversed_x, y), edges=(edge,)))
    nodes, edges = _snapshot(writer)
    assert nodes[code_ref_id("p", x.path)][0] == "csharp"
    assert edges == {(code_ref_id("p", x.path), code_ref_id("p", y.path), KbEdgeKind.IMPLEMENTS)}
    assert all("kb_edges" not in statement for statement in writes)

    writes.clear()
    changed_x = GraphNode(id=x.id, path=x.path, name=x.name, kind=x.kind, symbols=("class X : I", "int f(int x)"))
    database.sync("p", ExtractorGraph(version=1, nodes=(changed_x, y), edges=()))
    assert not _snapshot(writer)[1]
    assert any("kb_edges" in statement for statement in writes)


def test_missing_foreign_symbols_never_freeze_edges(stored: tuple[Writer, Engine, Path]) -> None:
    writer, _engine, root = stored
    _ = (root / "src/x.ts").write_text("import './y';")
    _ = (root / "src/y.ts").write_text("export {};")
    nodes = (
        GraphNode(id="ts:x", path="src/x.ts", name="x", kind="module"),
        GraphNode(id="ts:y", path="src/y.ts", name="y", kind="module"),
    )
    database = CodeGraphDatabase(writer)
    database.sync(
        "p", ExtractorGraph(version=1, nodes=nodes, edges=(GraphEdge(source="ts:x", target="ts:y", kind="imports"),))
    )
    _ = (root / "src/x.ts").write_text("export {};")
    database.sync("p", ExtractorGraph(version=1, nodes=nodes, edges=()))
    assert not _snapshot(writer)[1]


def test_hashing_rejects_external_paths_and_honors_python_encoding(stored: tuple[Writer, Engine, Path]) -> None:
    _writer, _engine, root = stored
    assert file_hashes(root, "../outside.py", "python", ()).body is None
    assert file_hashes(root, "missing.py", "python", ()).interface is None
    _ = (root / "src/latin.py").write_bytes(b'# coding: latin-1\ndef f(x="\xe9"):\n    return x\n')
    hashes = file_hashes(root, "src/latin.py", "python", ())
    assert hashes.body
    assert hashes.interface
    assert file_hashes(root, "src/y.py", "python", ()).interface == hashlib.sha256(b"").hexdigest()


def test_unknown_project_and_broken_edges_fail_atomically(stored: tuple[Writer, Engine, Path]) -> None:
    writer, _engine, _root = stored
    database = CodeGraphDatabase(writer)
    with pytest.raises(StoreWriteError, match="unknown project"):
        database.sync("missing", _graph())
    before = _snapshot(writer)
    invalid = ExtractorGraph(
        version=1, nodes=_graph().nodes, edges=(GraphEdge(source="x", target="unknown", kind="imports"),)
    )
    with pytest.raises(StoreWriteError, match="unknown node"):
        database.sync("p", invalid)
    assert _snapshot(writer) == before
