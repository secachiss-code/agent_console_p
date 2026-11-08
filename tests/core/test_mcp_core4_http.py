"""Real HTTP bearers cannot borrow another tab's lease or handover."""

from __future__ import annotations

import asyncio
import http.client
from typing import TYPE_CHECKING

from aconsole.contracts.app import (
    OpenTabParams,
    ProjectAddParams,
    ProjectView,
    TabIdParams,
    TabView,
    TerminalLaunchSpec,
)
from aconsole.contracts.common import AgentKind, WorkspaceMode
from tests.core.git_helpers import commit
from tests.core.test_claude_mcp import _config, _content, _Mcp
from tests.core.test_wire import _connect, _daemon, _git_repo, _stop

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_http_tab_token_selects_lease_owner_even_in_another_mcp_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    _ = commit(repo, "README.md", "start\n", "initial")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FAKE_HOME", str(home))

    async def scenario() -> None:
        daemon = _daemon(tmp_path)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            sessions = []
            for index in range(2):
                tab = await client.call(
                    "tab/open",
                    OpenTabParams(
                        project_id=project.id,
                        agent=AgentKind.CLAUDE,
                        workspace=WorkspaceMode.NEW_WORKTREE,
                        worktree_name=f"mcp-{index}",
                    ),
                    TabView,
                )
                spec = await client.call("tab/launchSpec", TabIdParams(tab_id=tab.id), TerminalLaunchSpec)
                _path, config = _config(spec.argv)
                session = _Mcp(config, spec.env)
                await asyncio.to_thread(session.start)
                sessions.append(session)

            async def call(index: int, name: str, arguments: dict[str, object]) -> dict[str, object]:
                return await asyncio.to_thread(
                    sessions[index].request,
                    "tools/call",
                    {
                        "name": name,
                        "arguments": arguments,
                    },
                )

            made = _content(
                await call(
                    0,
                    "plan_create",
                    {
                        "title": "Shared",
                        "goal": "Ship",
                        "nodes": [{"key": "N-1", "title": "One", "objective": "done"}],
                    },
                )
            )
            node = made["nodes"][0]["node_id"]
            claimed = _content(await call(0, "node_claim", {"node_id": node}))
            fence = claimed["fence"]
            _ = await call(0, "handover", {"action": "write", "goal": "Private packet"})
            for name, arguments in (
                ("node_update", {"node_id": node, "fence": fence, "status": "failed"}),
                ("node_attach", {"node_id": node, "fence": fence, "kind": "note", "ref": "foreign"}),
            ):
                denied = await call(1, name, arguments)
                assert denied["isError"]
                assert "LEASE_HELD" in str(denied)
            other = _content(await call(1, "handover", {"action": "read"}))
            assert other.get("goal", "") != "Private packet"
            assert not (await call(0, "node_update", {"node_id": node, "fence": fence})).get("isError", False)

            await asyncio.to_thread(_attack, sessions[0], sessions[1])
            assert not (await call(0, "node_update", {"node_id": node, "fence": fence})).get("isError", False)
            await asyncio.to_thread(_oversized, sessions[0])
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())


def _attack(owner: _Mcp, other: _Mcp) -> None:
    headers = dict(other.headers)
    headers["Mcp-Session-Id"] = owner.headers["Mcp-Session-Id"]
    for authenticated in (True, False):
        if not authenticated:
            del headers["Authorization"]
        connection = http.client.HTTPConnection(other.url.hostname, other.url.port, timeout=5)
        try:
            connection.request(
                "POST", other.url.path, b'{"jsonrpc":"2.0","id":9,"method":"tools/list"}', headers,
            )
            response = connection.getresponse()
            assert response.status == 403
            _ = response.read()
        finally:
            connection.close()


def _oversized(session: _Mcp) -> None:
    for chunked in (False, True):
        connection = http.client.HTTPConnection(session.url.hostname, session.url.port, timeout=5)
        payload = b" " * (1024 * 1024 + 1)
        try:
            if chunked:
                connection.request("POST", session.url.path, iter((payload,)), session.headers, encode_chunked=True)
            else:
                connection.request("POST", session.url.path, payload, session.headers)
            response = connection.getresponse()
            assert response.status == 413
            assert len(response.read()) < 4096
        finally:
            connection.close()
