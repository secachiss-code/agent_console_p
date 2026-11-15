"""Code graph snapshots share fact references and leave unrelated knowledge intact."""

from datetime import UTC, datetime
lazy from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.event import listen
lazy from sqlalchemy.orm import Session

from aconsole.core.application.ports.knowledge_store import StoredFact
from aconsole.core.application.ports.plan_store import StoreWriteError
from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph, GraphEdge, GraphNode
from aconsole.core.infrastructure.persistence.models import KbEdge, KbEdgeKind, KbNode, Project
from aconsole.core.infrastructure.persistence.repositories.code_graph import CodeGraphDatabase
from aconsole.core.infrastructure.persistence.repositories.knowledge import KnowledgeDatabase, code_ref_id
from tests.core.test_knowledge_store import _writer


def _graph(*, linked: bool = True) -> ExtractorGraph:
    return ExtractorGraph(version=1, nodes=(
        GraphNode(id="x", path="src/x.py", name="x", kind="module"),
        GraphNode(id="y", path="src/y.py", name="y", kind="module"),
    ), edges=(GraphEdge(source="x", target="y", kind="imports"),) if linked else ())


def test_incremental_graph_shares_fact_nodes_and_preserves_fact_edges(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    now = datetime.now(UTC)
    try:
        def seed(session: Session) -> None:
            session.add(Project(id="p", name="P", root_path="/p", languages=[], created_at=now))
        writer.call(seed)
        knowledge = KnowledgeDatabase(writer)
        knowledge.save_fact(StoredFact(id="kb-1", project_id="p", kind="fact", title="Uses x", body="",
                                      source="test", code_refs=("src/x.py",), stale=False,
                                      note_ref="test-note", updated_at=now), None)
        database = CodeGraphDatabase(writer)
        database.sync("p", _graph())
        writes: list[str] = []
        def capture(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
            if statement.startswith(("INSERT", "DELETE", "UPDATE")):
                writes.append(statement)
        listen(writer._engine, "before_cursor_execute", capture)
        database.sync("p", _graph())
        assert writes == []
        database.sync("p", _graph(linked=False))
        def check(session: Session) -> None:
            nodes = session.scalars(select(KbNode)).all()
            assert len(nodes) == 3
            assert sum(node.id == code_ref_id("p", "src/x.py") for node in nodes) == 1
            edges = session.scalars(select(KbEdge)).all()
            assert len(edges) == 1
            assert edges[0].kind is KbEdgeKind.CODE_REF
        writer.call(check)
        assert knowledge.load()[0][0].code_refs == ("src/x.py",)
    finally:
        writer.stop()


def test_unknown_project_fails_atomically(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    try:
        with pytest.raises(StoreWriteError, match="unknown project"):
            CodeGraphDatabase(writer).sync("missing", _graph())
    finally:
        writer.stop()
