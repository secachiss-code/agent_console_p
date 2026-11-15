"""Planning identity survives storage and reaches the real tab launch boundary."""

from dataclasses import replace
lazy from pathlib import Path

import msgspec
import pytest

from aconsole.contracts.app import ExitedParams, OpenTabParams, StartedParams, TabIdParams
from aconsole.contracts.common import AgentKind, ProjectId, WorkspaceMode
from aconsole.core.application.services.tabs import ProjectSeed, TabRequestError, TabService
from aconsole.core.domain.clock import FrozenClock
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
from aconsole.core.infrastructure.persistence.engine import write_engine
from aconsole.core.infrastructure.persistence.repositories.tabs import TabDatabase
from aconsole.core.infrastructure.persistence.startup import prepare_database, sqlite_url
from aconsole.core.infrastructure.persistence.writer import Writer
from tests.core.conftest import FakeTabPorts
from tests.core.test_tabs_restore import _WHEN, _Memory, _row


@pytest.mark.parametrize("agent", [AgentKind.CLAUDE, AgentKind.CODEX])
def test_planning_launch_model_prompt_and_restore(agent: AgentKind) -> None:
    ports = FakeTabPorts()
    store = _Memory()
    service = TabService(FrozenClock(_WHEN), ports)
    service.use_store(store)
    service.add_project(ProjectSeed(project_id="proj", root="/repo", container="agentbox"))
    plan = demo_snapshot().plans[0]
    service.plan_context = lambda _project: (plan,)
    opened = service.open_tab(
        OpenTabParams(
            project_id=ProjectId("proj"),
            agent=agent,
            workspace=WorkspaceMode.MAIN_CHECKOUT,
            mode="plan",
            model="explicit-model",
            title="Adapt AConsole",
        )
    )
    spec = service.launch_spec(TabIdParams(tab_id=opened.id))
    assert spec.argv[spec.argv.index("--model") + 1] == "explicit-model"
    assert "Adapt AConsole" in spec.argv[-1]
    assert plan.goal in spec.argv[-1]
    assert plan.nodes[0].title in spec.argv[-1]
    assert "test_cmd" in spec.argv[-1]
    assert opened.mode == "plan"
    assert opened.model == "explicit-model"
    fresh = TabService(FrozenClock(_WHEN), ports)
    fresh.use_store(store)
    fresh.reload()
    restored = fresh.snapshot().tabs[0]
    assert restored.mode == "plan"
    assert restored.model == "explicit-model"
    resumed = fresh.launch_spec(TabIdParams(tab_id=restored.id))
    assert resumed.argv[resumed.argv.index("--model") + 1] == "explicit-model"
    fresh.started(StartedParams(tab_id=restored.id, pid=42))
    if agent is AgentKind.CODEX:
        fresh.codex_session(restored.worktree_path, "native-session")
    else:
        fresh.reported_session(str(restored.id), "native-session")
    forked = fresh.fork(TabIdParams(tab_id=restored.id))
    assert forked.mode == "plan"
    assert forked.model == "explicit-model"
    fresh.exited(ExitedParams(tab_id=restored.id, wait_status=0))
    fork_spec = fresh.launch_spec(TabIdParams(tab_id=forked.id))
    assert fork_spec.argv[fork_spec.argv.index("--model") + 1] == "explicit-model"
    assert not any("Adapt the application plan" in arg for arg in fork_spec.argv)


def test_planning_contract_refuses_missing_model_before_open() -> None:
    with pytest.raises(ValueError, match="requires a model"):
        OpenTabParams(
            project_id=ProjectId("proj"), agent=AgentKind.CODEX, workspace=WorkspaceMode.MAIN_CHECKOUT, mode="plan"
        )
    with pytest.raises(msgspec.ValidationError, match="requires a model"):
        msgspec.json.decode(
            b'{"project_id":"proj","agent":"codex","workspace":"main_checkout","mode":"plan"}',
            type=OpenTabParams,
        )


def test_planning_refuses_unsupported_agent_before_creating_workspace() -> None:
    ports = FakeTabPorts()
    service = TabService(FrozenClock(_WHEN), ports)
    service.add_project(ProjectSeed(project_id="proj", root="/repo", container="agentbox"))
    with pytest.raises(TabRequestError, match="AGENT_UNAVAILABLE"):
        service.open_tab(
            OpenTabParams(
                project_id=ProjectId("proj"),
                agent=AgentKind.OPENCODE,
                workspace=WorkspaceMode.NEW_WORKTREE,
                mode="plan",
                model="m",
            )
        )
    assert not ports.created
    assert not ports.ensured
    assert not service.snapshot().tabs


def test_planning_mode_and_model_round_trip_sqlite(tmp_path: Path) -> None:
    database = tmp_path / "tabs.db"
    prepare_database(database, now=_WHEN)
    engine = write_engine(sqlite_url(database))
    writer = Writer(engine)
    writer.start()
    try:
        store = TabDatabase(writer)
        row = replace(_row("tab-1", title="Planning"), mode="plan", model="m-first")
        store.save(row)
        assert store.load_open()[0].mode == "plan"
        assert store.load_open()[0].model == "m-first"
        store.save(replace(row, model="m-updated"))
        assert store.load_open()[0].model == "m-updated"
    finally:
        writer.stop()
        engine.dispose()
