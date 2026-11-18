"""The code graph: local Python imports, other languages through the runner, and `knowledge/impact`."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec
import pytest

from aconsole.contracts.app import ImpactParams, ImpactView, ProjectAddParams, ProjectView
from aconsole.contracts.common import ProjectId
from aconsole.contracts.errors import ErrorData, ErrorObject
from aconsole.contracts.ipc import ResponseErr, ResponseOk
from aconsole.core.bootstrap import Daemon
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.codegraph.index import CodeGraphIndex
from aconsole.core.infrastructure.codegraph.local_python import scan_python
from aconsole.core.infrastructure.codegraph.protocol import ExtractorGraph, GraphEdge, GraphNode
from aconsole.core.infrastructure.codegraph.runner import ExtractorRun, ExtractorRunner
from aconsole.core.infrastructure.mcp.tools import PlanBoard, TabContext
from aconsole.core.infrastructure.persistence.engine import migrate, write_engine
from aconsole.core.infrastructure.persistence.writer import Writer
from aconsole.devcli.client import request_frame
from tests.core.conftest import wait_for_socket
from tests.socket_paths import short_socket_path

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def git_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    _ = subprocess.run(["/usr/bin/git", "init", "-q", "-b", "trunk", str(root)], check=True)
    return root


def writer_factory(tmp_path: Path) -> Writer:
    url = f"sqlite:///{tmp_path / 'state.db'}"
    migrate(url, "head")
    writer = Writer(write_engine(url))
    writer.start()
    return writer


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
    _ = (tmp_path / "boom.py").write_text("raise SystemExit(9)\nopen('ran', 'w')\n", encoding="utf-8")
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
    _ = (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    runner = _Runner(ExtractorRun(graph=_RUST, error=None))
    now = [0.0]
    index = CodeGraphIndex(runner, lambda _project: tmp_path, clock=lambda: now[0], ttl_s=10)
    assert index.resolve("p", "a.rs") == "rs:a"
    assert index.dependents("p") == {"rs:a": ("rs:b",)}
    assert runner.calls == [("rust-metadata", tmp_path)]
    now[0] = 11.0
    _ = index.graph("p")
    assert len(runner.calls) == 2


def test_a_python_project_merges_the_package_json_graph(tmp_path: Path) -> None:
    _write(tmp_path)
    _ = (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    _ = (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    runner = _Runner(ExtractorRun(graph=_RUST, error=None))
    index = CodeGraphIndex(runner, lambda _project: tmp_path)
    assert index.resolve("p", "pkg.base") == "pkg.base"
    assert index.resolve("p", "ts:a") == "ts:a"
    assert runner.calls == [("ts", tmp_path)]


def test_a_failed_extractor_is_a_contract_error(tmp_path: Path) -> None:
    _ = (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    error = ErrorObject(
        code=5006,
        message="hostrun is missing",
        data=ErrorData(reason="x", domain="aconsole.core", retryable=True, user_message_key="k", details=None),
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
    repo = git_repo(tmp_path / "repo")
    _write(repo)

    async def scenario() -> None:
        daemon = Daemon(short_socket_path(tmp_path / "core.sock"), database=tmp_path / "core.db", idle_s=30)
        daemon.serve_ingest = False
        task = asyncio.create_task(daemon.serve())
        await asyncio.sleep(0.05)
        if task.done():
            _ = task.result()
        await wait_for_socket(daemon.socket)

        async def call[T: msgspec.Struct](method: str, params: msgspec.Struct, result: type[T]) -> T:
            response = await asyncio.to_thread(request_frame, str(daemon.socket), method, params)
            assert isinstance(response, ResponseOk)
            return msgspec.json.decode(response.result, type=result)

        async def error(method: str, params: msgspec.Struct) -> str:
            response = await asyncio.to_thread(request_frame, str(daemon.socket), method, params)
            assert isinstance(response, ResponseErr)
            return response.error.message

        project = await call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        impact = await call(
            "knowledge/impact",
            ImpactParams(project_id=project.id, target="src/pkg/base.py"),
            ImpactView,
        )
        assert impact.target == "pkg.base"
        assert impact.layers[0] == ("pkg.mid",)
        assert set(impact.layers[1]) == {"pkg.sub", "pkg.top"}
        assert impact.layers[2] == ("other",)
        one = await call(
            "knowledge/impact",
            ImpactParams(project_id=project.id, target="pkg.base", hops=1),
            ImpactView,
        )
        assert len(one.layers) == 1
        assert (
            await error(
                "knowledge/impact",
                ImpactParams(project_id=project.id, target="no.such"),
            )
            == "IMPACT_TARGET_UNKNOWN"
        )
        assert (
            await error(
                "knowledge/impact",
                ImpactParams(project_id=ProjectId("gone"), target="x"),
            )
            == "IMPACT_TARGET_UNKNOWN"
        )
        board = PlanBoard()
        board.code_graph = daemon.knowledge.code_graph
        ctx = TabContext(tab_id="t1", project_id=str(project.id), worktree=None, agent="claude")
        tool = await asyncio.to_thread(board.impact, ctx, "pkg.base", 1, 50, None)
        assert tool.layers == [["pkg.mid"]]
        daemon.request_stop()
        assert await task == 0

    asyncio.run(scenario())


def test_persistent_cte_matches_memory_layers_and_is_used_by_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    from aconsole.core.infrastructure.persistence.models import Project
    from aconsole.core.infrastructure.persistence.repositories.code_graph import CodeGraphDatabase

    _write(tmp_path)
    writer = writer_factory(tmp_path)
    try:

        def seed(session: Session) -> None:
            session.add(Project(id="p", name="P", root_path=str(tmp_path), languages=[], created_at=datetime.now(UTC)))

        _ = writer.call(seed)
        store = CodeGraphDatabase(writer)

        index = CodeGraphIndex(_Runner(ExtractorRun(graph=None, error=None)), lambda _p: tmp_path, store=store)

        board = PlanBoard()
        board.code_graph = index
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

    writer = writer_factory(tmp_path)
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

        _ = writer.call(seed)
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
            _ = writer.call(lambda session: impact_rows(session, "p", "a", 4, 50))
    finally:
        writer.stop()


@pytest.mark.parametrize("persistent", [False, True])
def test_forward_lod_uses_shortest_distance_allowed_edges_and_project_isolation(
    tmp_path: Path, *, persistent: bool
) -> None:
    from datetime import UTC, datetime

    from aconsole.core.infrastructure.persistence.models import Project
    from aconsole.core.infrastructure.persistence.repositories.code_graph import CodeGraphDatabase, forward_dependencies
    from aconsole.core.infrastructure.persistence.repositories.knowledge import code_ref_id

    graph = ExtractorGraph(
        version=1,
        nodes=tuple(
            GraphNode(id=name, path=f"{name}.ts", name=name, kind="module", symbols=(f"class {name}",))
            for name in "abcdefxyz"
        ),
        edges=tuple(
            GraphEdge(source=source, target=target, kind=kind)
            for source, target, kind in (
                ("a", "b", "imports"),
                ("a", "c", "imports"),
                ("b", "d", "depends_on"),
                ("c", "d", "inherits"),
                ("d", "e", "implements"),
                ("e", "f", "imports"),
                ("d", "a", "imports"),
                ("x", "a", "imports"),
                ("a", "y", "uses_type"),
                ("a", "z", "links"),
            )
        ),
    )
    _ = (tmp_path / "package.json").write_text("{}")
    writer = writer_factory(tmp_path)
    try:

        def seed(session: Session) -> None:
            for project in ("p", "q"):
                session.add(
                    Project(
                        id=project,
                        name=project,
                        root_path=str(tmp_path / project),
                        languages=[],
                        created_at=datetime.now(UTC),
                    )
                )

        _ = writer.call(seed)
        store = CodeGraphDatabase(writer)
        store.sync("q", graph)
        index = CodeGraphIndex(
            _Runner(ExtractorRun(graph=graph, error=None)), lambda _p: tmp_path, store=store if persistent else None
        )
        actual = forward_dependencies(index, "p", ("a.ts",))
        assert [(node.path, depth) for node, depth in actual] == [
            ("b.ts", 1),
            ("c.ts", 1),
            ("d.ts", 2),
            ("e.ts", 3),
        ]
        combined = forward_dependencies(index, "p", ("a.ts", "d.ts"))
        assert [(node.path, depth) for node, depth in combined] == [
            ("b.ts", 1),
            ("c.ts", 1),
            ("e.ts", 1),
            ("f.ts", 2),
        ]
        assert forward_dependencies(index, "p", ("absent.ts",)) == ()
        assert store.dependencies("q", (code_ref_id("p", "a.ts"),)) == []
    finally:
        writer.stop()


def test_pipeline_context_delivers_lod_from_actual_worktree(tmp_path: Path) -> None:
    from dataclasses import replace

    from aconsole.core.application.services.context_shaper import ContextShaper
    from aconsole.core.application.services.gates import MicroProfile
    from aconsole.core.application.services.pipeline import PipelineActor, PipelineContract
    from aconsole.core.infrastructure.pipeline import PipelineGit
    from tests.core.application.test_context_shaper import DIRECTION, ENVELOPE, LEASE, MODULE, SESSION, Prefix, Scratch

    repo = git_repo(tmp_path / "repo")
    for name, text in {
        "a.py": "import b\ndef target():\n    return 'TARGET_BODY'\n",
        "b.py": "import c\ndef neighbor(value: int) -> str:\n    return 'NEIGHBOR_BODY'\n",
        "c.py": "import d\nclass Second: pass\n",
        "d.py": "import e\nclass Third: pass\n",
        "e.py": "class Fourth: pass\n",
        "caller.py": "import a\nclass Caller: pass\n",
    }.items():
        _ = (repo / name).write_text(text)
    index = CodeGraphIndex(_Runner(ExtractorRun(graph=None, error=None)), lambda _p: repo)
    workspace = PipelineGit(index)
    paths = ("a.py",)
    contract = PipelineContract(
        plan_id="p",
        node_id="n",
        direction=replace(DIRECTION, scope=("*.py",)),
        module=replace(MODULE, paths=paths),
        lease=LEASE,
        profile=MicroProfile(format=("ruff",), fix=("ruff",), lint=("ruff",), types=("pyright",), tests=("pytest",)),
        gates_sha256="g",
    )
    direction, sliced = workspace.context(PipelineActor(tab_id="t", project_id="p", worktree=repo), contract, ())
    assert sliced is not None
    assert "TARGET_BODY" in sliced
    result = ContextShaper(Scratch()).shape(
        session=SESSION,
        direction=direction,
        module=contract.module,
        envelope=replace(ENVELOPE, scope=paths),
        lease=LEASE,
        file_slice=sliced,
    )
    assert result.direction_prefix is not None
    dependencies = msgspec.json.decode(result.direction_prefix, type=Prefix).dependencies
    assert [(item.path, item.distance) for item in dependencies] == [("b.py", 1), ("c.py", 2), ("d.py", 3)]
    assert "def neighbor(value: int) -> str:" in dependencies[0].text
    assert all("\n" not in item.text for item in dependencies[1:])
    assert "NEIGHBOR_BODY" not in result.direction_prefix
    assert "e.py" not in result.direction_prefix
    assert "caller.py" not in result.direction_prefix


@pytest.mark.parametrize("persistent", [False, True])
def test_forward_lod_caps_files_at_forty_in_distance_then_path_order(tmp_path: Path, *, persistent: bool) -> None:
    from datetime import UTC, datetime

    from aconsole.core.infrastructure.persistence.models import Project
    from aconsole.core.infrastructure.persistence.repositories.code_graph import CodeGraphDatabase, forward_dependencies

    graph = ExtractorGraph(
        version=1,
        nodes=tuple(
            GraphNode(id=str(number), path=f"{number:02}.ts", name=str(number), kind="module", symbols=("export X",))
            for number in reversed(range(46))
        ),
        edges=tuple(GraphEdge(source="0", target=str(number), kind="imports") for number in reversed(range(1, 46))),
    )
    _ = (tmp_path / "package.json").write_text("{}")
    writer = writer_factory(tmp_path)
    try:
        _ = writer.call(
            lambda session: session.add(
                Project(id="p", name="p", root_path=str(tmp_path), languages=[], created_at=datetime.now(UTC))
            )
        )
        index = CodeGraphIndex(
            _Runner(ExtractorRun(graph=graph, error=None)),
            lambda _p: tmp_path,
            store=CodeGraphDatabase(writer) if persistent else None,
        )
        actual = forward_dependencies(index, "p", ("00.ts",))
        assert [(node.path, depth) for node, depth in actual] == [(f"{number:02}.ts", 1) for number in range(1, 41)]
        assert all(node.symbols == ("export X",) for node, _depth in actual)
    finally:
        writer.stop()
