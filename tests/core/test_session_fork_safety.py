"""A fork must not become a plain resume of its parent after core restart."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aconsole.contracts.app import OpenTabParams, RestartParams, StartedParams, TabIdParams
from aconsole.contracts.common import AgentKind, ProjectId, WorkspaceMode
from aconsole.core.application.services.tabs import ProjectSeed, TabRequestError, TabService
from aconsole.core.domain.clock import FrozenClock
from tests.core.conftest import FakeTabPorts


def test_pending_fork_survives_serialization_and_keeps_parent_session() -> None:
    clock = FrozenClock(datetime(2026, 11, 7, tzinfo=UTC))
    seed = ProjectSeed(project_id="proj", root="/repo", container="box", sessions=frozenset({"parent"}))
    service = TabService(clock, FakeTabPorts())
    service.add_project(seed)
    source = service.open_tab(OpenTabParams(
        project_id=ProjectId("proj"), agent=AgentKind.CLAUDE,
        workspace=WorkspaceMode.MAIN_CHECKOUT, resume_session="parent",
    ))
    copy = service.fork(TabIdParams(tab_id=source.id))
    assert copy.agent_session_id is None, "the parent identity is not advertised as the fork identity"
    saved = service._export(service._tabs[str(copy.id)])
    restored = TabService(clock, FakeTabPorts())
    restored.add_project(seed)
    restored._adopt(saved)
    command = restored.launch_spec(TabIdParams(tab_id=copy.id)).argv
    assert "--fork-session" in command
    restored.reported_session(str(copy.id), "fork-child")
    command = restored.launch_spec(TabIdParams(tab_id=copy.id)).argv
    assert "--fork-session" not in command
    assert command[-2:] == ("--resume", "fork-child")
    assert service.snapshot().tabs[0].agent_session_id == "parent"


def test_resume_without_identity_refuses_before_stopping_the_agent() -> None:
    service = TabService(FrozenClock(datetime(2026, 11, 7, tzinfo=UTC)), FakeTabPorts())
    service.add_project(ProjectSeed(project_id="proj", root="/repo", container="box"))
    tab = service.open_tab(OpenTabParams(
        project_id=ProjectId("proj"), agent=AgentKind.CLAUDE, workspace=WorkspaceMode.MAIN_CHECKOUT,
    ))
    service.started(StartedParams(tab_id=tab.id, pid=123))
    with pytest.raises(TabRequestError, match="SESSION_NOT_FOUND"):
        service.restart(RestartParams(tab_id=tab.id, resume=True))
    assert service.snapshot().tabs[0].started
    with pytest.raises(TabRequestError, match="SESSION_NOT_FOUND"):
        service.fork(TabIdParams(tab_id=tab.id))
