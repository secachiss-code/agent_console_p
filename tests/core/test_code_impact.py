"""The code graph: local Python imports, other languages through the runner, and `knowledge/impact`."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from aconsole.contracts.app import ImpactParams, ImpactView, ProjectAddParams, ProjectView
from aconsole.contracts.common import ProjectId
from aconsole.contracts.errors import ErrorData, ErrorObject
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.codegraph.index import CodeGraphIndex
from aconsole.core.infrastructure.codegraph.local_python import scan_python
from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph, GraphEdge, GraphNode
from aconsole.core.infrastructure.codegraph.runner import ExtractorRun, ExtractorRunner
from aconsole.core.infrastructure.mcp.tools import PlanBoard, TabContext
from tests.core.test_wire import _connect, _daemon, _git_repo, _stop

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_FILES = {
    "src/pkg/__init__.py": "",
    "src/pkg/base.py": "VALUE = 1\n",
    "src/pkg/mid.py": "from pkg import base\nfrom pkg.base import VALUE\n",
    "src/pkg/top.py": "from . import mid\n",
    "src/pkg/sub/__init__.py": "from ..mid import x\n",
    "other.py": "import pkg.top\nimport os\n",
    "broken.py": "def (\n",
    ".venv/lib/skip.py": "import pkg.base\n",
}


def _write(root: Path) -> None:
    for name, text in _FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(text, encoding="utf-8")


def test_scan_reads_imports_without_running_anything(tmp_path: Path) -> None:
    _write(tmp_path)
    (tmp_path / "boom.py").write_text("raise SystemExit(9)\nopen('ran', 'w')\n", encoding="utf-8")
    graph = scan_python(tmp_path)
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("pkg.mid", "pkg.base") in edges
    assert ("pkg.top", "pkg.mid") in edges
    assert ("pkg.sub", "pkg.mid") in edges
    assert ("other", "pkg.top") in edges
    names = {node.id for node in graph.nodes}
    assert "broken" in names
    assert not any("skip" in name for name in names)
    assert not (tmp_path / "ran").exists()


class _Runner:
    def __init__(self, run: ExtractorRun) -> None:
        self.result: ExtractorRun = run
        self.calls: list[tuple[str, Path | None]] = []

    def run(self, language: str, path: str, *, cwd: Path | None = None) -> ExtractorRun:
        assert path == "."
        self.calls.append((language, cwd))
        return self.result


_RUST = ExtractorGraph(
    version=1,
    nodes=(
        GraphNode(id="a", path="a.rs", name="a", kind="crate"),
        GraphNode(id="b", path="b.rs", name="b", kind="crate"),
    ),
    edges=(GraphEdge(source="b", target="a", kind="depends_on"),),
)


def test_another_language_uses_the_runner_and_is_cached(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    runner = _Runner(ExtractorRun(graph=_RUST, error=None))
    now = [0.0]
    index = CodeGraphIndex(runner, lambda _project: tmp_path, clock=lambda: now[0], ttl_s=10)
    assert index.resolve("p", "a.rs") == "a"
    assert index.dependents("p") == {"a": ("b",)}
    assert runner.calls == [("rust-metadata", tmp_path)]
    now[0] = 11.0
    _ = index.graph("p")
    assert len(runner.calls) == 2


def test_a_python_project_stays_python_with_a_package_json(tmp_path: Path) -> None:
    _write(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    runner = _Runner(ExtractorRun(graph=_RUST, error=None))
    index = CodeGraphIndex(runner, lambda _project: tmp_path)
    assert index.resolve("p", "pkg.base") == "pkg.base"
    assert runner.calls == []


def test_a_failed_extractor_is_a_contract_error(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    error = ErrorObject(
        code=5006,
        message="hostrun is missing",
        data=ErrorData(reason="x", domain="d", retryable=True, user_message_key="k", details=None),
    )
    index = CodeGraphIndex(_Runner(ExtractorRun(graph=None, error=error)), lambda _project: tmp_path)
    with pytest.raises(DomainError) as caught:
        _ = index.graph("p")
    assert caught.value.name == "CODEGRAPH_EXTRACTOR_FAILED"


def test_default_hostrun_is_not_tied_to_one_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACONSOLE_HOSTRUN", "/opt/hostrun")
    assert ExtractorRunner().argv("ts", ".")[0] == "/opt/hostrun"
    monkeypatch.delenv("ACONSOLE_HOSTRUN")
    assert ExtractorRunner().argv("ts", ".")[0].startswith(str(Path.home()))


def test_impact_over_the_wire_and_for_agents(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    _write(repo)

    async def scenario() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        await asyncio.sleep(0.05)
        if task.done():
            task.result()
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        impact = await client.call(
            "knowledge/impact",
            ImpactParams(project_id=project.id, target="src/pkg/base.py"),
            ImpactView,
        )
        assert impact.target == "pkg.base"
        assert impact.layers[0] == ("pkg.mid",)
        assert set(impact.layers[1]) == {"pkg.sub", "pkg.top"}
        assert impact.layers[2] == ("other",)
        one = await client.call(
            "knowledge/impact",
            ImpactParams(project_id=project.id, target="pkg.base", hops=1),
            ImpactView,
        )
        assert len(one.layers) == 1
        assert (
            await client.error(
                "knowledge/impact",
                ImpactParams(project_id=project.id, target="no.such"),
            )
            == "IMPACT_TARGET_UNKNOWN"
        )
        assert (
            await client.error(
                "knowledge/impact",
                ImpactParams(project_id=ProjectId("gone"), target="x"),
            )
            == "IMPACT_TARGET_UNKNOWN"
        )
        board: PlanBoard = daemon._plans
        ctx = TabContext(tab_id="t1", project_id=str(project.id), worktree=None, agent="claude")
        tool = await asyncio.to_thread(board.impact, ctx, "pkg.base", 1, 50, None)
        assert tool.layers == [["pkg.mid"]]
        await _stop(daemon, task, client)

    asyncio.run(scenario())


def test_persistent_cte_matches_memory_layers_and_is_used_by_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    from aconsole.core.infrastructure.persistence.models import Project
    from aconsole.core.infrastructure.persistence.repositories.code_graph import CodeGraphDatabase
    from tests.core.test_knowledge_store import _writer

    _write(tmp_path)
    writer = _writer(tmp_path)
    try:

        def seed(session: Session) -> None:
            session.add(Project(id="p", name="P", root_path=str(tmp_path), languages=[], created_at=datetime.now(UTC)))

        writer.call(seed)
        store = CodeGraphDatabase(writer)

        index = CodeGraphIndex(_Runner(ExtractorRun(graph=None, error=None)), lambda _p: tmp_path, store=store)

        class StoredIndex:
            # Model the public getter pending addition to CodeGraphIndex's scope.
            @property
            def store(self) -> CodeGraphDatabase:
                return store

            def graph(self, project_id: str) -> ExtractorGraph:
                return index.graph(project_id)

            def resolve(self, project_id: str, target: str) -> str | None:
                return index.resolve(project_id, target)

            def dependents(self, project_id: str, kinds: frozenset[str] | None = None) -> dict[str, tuple[str, ...]]:
                return index.dependents(project_id, kinds)

        board = PlanBoard()
        board.code_graph = StoredIndex()
        ctx = TabContext(tab_id="t", project_id="p", worktree=None, agent="codex")
        from aconsole.core.infrastructure.mcp.tools import impact_layers

        for hops in (1, 2, 3):
            expected, truncated = impact_layers(index.dependents("p"), "pkg.base", hops, 50)
            actual = board.impact(ctx, "src/pkg/base.py", hops, 50, None)
            assert [set(layer) for layer in actual.layers] == [set(layer) for layer in expected]
            assert actual.truncated is truncated

        def no_python_traversal(*_args: object) -> None:
            pytest.fail("Persisted graph must use the CTE")

        monkeypatch.setattr("aconsole.core.infrastructure.mcp.tools.impact_layers", no_python_traversal)
        assert board.impact(ctx, "pkg.base", 3, 50, None).layers[-1] == ["other"]
        assert board.impact(ctx, "pkg.base", 3, 1, None).truncated
        assert board.impact(ctx, "pkg.base", 3, 50, ["inherits"]).layers == []
    finally:
        writer.stop()


def test_persistent_cte_handles_diamonds_cycles_and_project_isolation(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from aconsole.core.infrastructure.persistence.models import Project
    from aconsole.core.infrastructure.persistence.repositories.code_graph import CodeGraphDatabase, impact_rows
    from aconsole.core.infrastructure.persistence.repositories.knowledge import code_ref_id
    from tests.core.test_knowledge_store import _writer

    writer = _writer(tmp_path)
    graph = ExtractorGraph(
        version=1,
        nodes=tuple(GraphNode(id=name, path=f"{name}.py", name=name, kind="module") for name in "abcde"),
        edges=tuple(
            GraphEdge(source=source, target=target, kind="imports")
            for source, target in (
                ("b", "a"),
                ("c", "a"),
                ("d", "b"),
                ("d", "c"),
                ("a", "d"),
                ("e", "d"),
            )
        ),
    )
    try:

        def seed(session: Session) -> None:
            for project in ("p", "q"):
                session.add(
                    Project(
                        id=project, name=project, root_path=f"/{project}", languages=[], created_at=datetime.now(UTC)
                    )
                )

        writer.call(seed)
        store = CodeGraphDatabase(writer)
        store.sync("p", graph)
        store.sync("q", graph)
        rows, truncated = store.impact("p", code_ref_id("p", "a.py"), 3, 50)
        expected = {code_ref_id("p", f"{name}.py"): depth for name, depth in (("b", 1), ("c", 1), ("d", 2), ("e", 3))}
        assert dict(rows) == expected
        assert not truncated
        assert store.impact("q", code_ref_id("p", "a.py"), 3, 50) == ([], False)
        assert store.impact("p", code_ref_id("p", "a.py"), 0, 50) == ([], False)
        assert store.impact("p", code_ref_id("p", "a.py"), 3, 0) == ([], True)
        with pytest.raises(ValueError, match=r"0\.\.3 hops"):
            writer.call(lambda session: impact_rows(session, "p", "a", 4, 50))
    finally:
        writer.stop()
