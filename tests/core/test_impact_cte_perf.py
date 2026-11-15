"""Indexed reverse traversal budget on a persistent graph containing 50,000 edges."""

from datetime import UTC, datetime
from time import perf_counter
lazy from pathlib import Path

import pytest
from sqlalchemy import insert
lazy from sqlalchemy.orm import Session

from aconsole.core.infrastructure.persistence.models import KbEdge, KbNode, Project
from aconsole.core.infrastructure.persistence.repositories.code_graph import impact_rows
from tests.core.test_knowledge_store import _writer


@pytest.mark.performance
def test_three_hops_over_fifty_thousand_edges_under_fifty_ms(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    now = datetime.now(UTC)
    timings: list[float] = []
    try:

        def scenario(session: Session) -> None:
            session.add(Project(id="p", name="P", root_path="/p", languages=[], created_at=now))
            session.flush()
            session.execute(
                insert(KbNode),
                [
                    {
                        "id": f"n{number:05}",
                        "project_id": "p",
                        "node_type": "module",
                        "title": str(number),
                        "is_stale": False,
                        "updated_at": now,
                    }
                    for number in range(50_001)
                ],
            )
            session.execute(
                insert(KbEdge),
                [
                    {
                        "src_id": f"n{number:05}",
                        "dst_id": f"n{(number - 1) // 10:05}",
                        "kind": "imports",
                        "origin": "codegraph",
                    }
                    for number in range(1, 50_001)
                ],
            )
            session.flush()
            for _ in range(10):
                start = perf_counter()
                rows, truncated = impact_rows(session, "p", "n00000", 3, 2000)
                timings.append((perf_counter() - start) * 1000)
                assert len(rows) == 1110
                assert not truncated
                assert max(depth for _, depth in rows) == 3

        writer.call(scenario)
        assert max(timings) <= 50, f"CTE latency ms: {timings}"
    finally:
        writer.stop()
