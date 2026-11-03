"""Project and first-task requests for first-run setup."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import override

import msgspec

from aconsole.contracts.app import (
    EmptyResult,
    OpenTabParams,
    ProjectAddParams,
    ProjectIdParams,
    ProjectView,
    TabView,
)
from aconsole.contracts.common import AgentKind, WorkspaceMode
from aconsole.ui.client.core_client import CoreClient


@dataclass(frozen=True)
class _Request:
    method: str
    params: msgspec.Struct
    result_type: type[msgspec.Struct]


class _ProjectsClient(CoreClient):
    def __init__(self, responses: dict[str, msgspec.Struct]) -> None:
        super().__init__("unused")
        self.responses = responses
        self.requests: list[_Request] = []

    @override
    async def request[T: msgspec.Struct](
        self,
        method: str,
        params: msgspec.Struct,
        result_type: type[T],
    ) -> T:
        self.requests.append(_Request(method, params, result_type))
        return msgspec.convert(self.responses[method], type=result_type)


def test_b10_setup_add_project_uses_exact_root_and_project_result() -> None:
    from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
    from aconsole.ui.viewmodels.setup_vm import SetupViewModel

    project = demo_snapshot().projects[0]
    client = _ProjectsClient({"project/add": project})
    viewmodel = SetupViewModel(client)

    result = asyncio.run(viewmodel.add_project("/home/test/Project"))

    assert result == project
    assert client.requests == [
        _Request(
            "project/add",
            ProjectAddParams(root="/home/test/Project"),
            ProjectView,
        )
    ]


def test_b10_setup_select_project_uses_typed_active_project_request() -> None:
    from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
    from aconsole.ui.viewmodels.setup_vm import SetupViewModel

    project_id = demo_snapshot().projects[1].id
    client = _ProjectsClient({"project/setActive": EmptyResult()})
    viewmodel = SetupViewModel(client)

    asyncio.run(viewmodel.select_project(project_id))

    assert client.requests == [
        _Request(
            "project/setActive",
            ProjectIdParams(project_id=project_id),
            EmptyResult,
        )
    ]


def test_b10_setup_start_first_task_uses_new_worktree_and_returns_tab() -> None:
    from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
    from aconsole.ui.viewmodels.setup_vm import SetupViewModel

    project_id = demo_snapshot().projects[0].id
    tab = demo_snapshot().tabs[0]
    client = _ProjectsClient({"tab/open": tab})
    viewmodel = SetupViewModel(client)

    result = asyncio.run(viewmodel.start_first_task(project_id, AgentKind.CLAUDE))

    assert result == tab
    assert client.requests == [
        _Request(
            "tab/open",
            OpenTabParams(
                project_id=project_id,
                agent=AgentKind.CLAUDE,
                workspace=WorkspaceMode.NEW_WORKTREE,
            ),
            TabView,
        )
    ]
