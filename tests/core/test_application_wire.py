"""UI facade -> real IPC core -> HTTP hook/MCP -> UI events and persisted plan.

Only the external Podman executable is a fixture. No fake core, seeded plan, direct
broker decision or in-process MCP tool call participates in this workflow.
"""

from __future__ import annotations

import asyncio
import json
from http.client import HTTPConnection
from typing import TYPE_CHECKING

import msgspec
import pytest

from aconsole.contracts.app import PlanIdParams, PlanListParams, ProjectAddParams, TabIdParams
from aconsole.contracts.common import ApprovalDecision, ApprovalScope, NodeStatus, PlanId
from aconsole.contracts.ingest import HookResponse
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.client.typed_facade import TypedFacade
from aconsole.ui.viewmodels.approvals_vm import ApprovalsViewModel
from tests.core.conftest import wait_for_socket
from tests.core.test_approval_hold import _post_daemon_permission_hook
from tests.core.test_approval_window import _gone, _held
from tests.core.test_wire import _daemon, _git_repo, _open

if TYPE_CHECKING:
    from pathlib import Path

    from aconsole.contracts.app import ProjectView


class _HttpMcp:
    def __init__(self, port: int, token: str) -> None:
        self.port = port
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self.ident = 0

    def request(
        self,
        method: str,
        params: dict[str, object],
        *,
        notification: bool = False,
    ) -> dict[str, object]:
        self.ident += 1
        message = {"jsonrpc": "2.0", "id": self.ident, "method": method, "params": params}
        if notification:
            del message["id"]
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("POST", "/mcp", json.dumps(message), self.headers)
            response = connection.getresponse()
            body = response.read()
            if notification:
                assert response.status == 202, body
                return {}
            assert response.status == 200, body
            session_id = response.getheader("Mcp-Session-Id")
            if session_id is not None:
                self.headers["Mcp-Session-Id"] = session_id
            envelope = json.loads(body)
            assert envelope["id"] == self.ident
            assert "error" not in envelope, envelope
            return envelope["result"]
        finally:
            connection.close()

    def initialize(self) -> None:
        result = self.request(
            "initialize",
            {
                "protocolVersion": "2026-07-28",
                "capabilities": {},
                "clientInfo": {"name": "application-wire", "version": "0"},
            },
        )
        self.headers["MCP-Protocol-Version"] = str(result["protocolVersion"])
        self.request("notifications/initialized", {}, notification=True)

    def tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        assert not result.get("isError", False), result
        text = next(item["text"] for item in result["content"] if item["type"] == "text")
        return json.loads(text)


async def _exercise_http_plan(client: CoreClient, project: ProjectView, mcp: _HttpMcp) -> PlanId:
    await asyncio.to_thread(mcp.initialize)
    created = await asyncio.to_thread(
        mcp.tool,
        "plan_create",
        {
            "title": "HTTP plan",
            "goal": "Reach the UI",
            "nodes": [
                {"key": "WIRE-1", "title": "First task", "objective": "Prove the transport"},
            ],
        },
    )
    plan_id = PlanId(str(created["plan_id"]))
    facade = TypedFacade(client)
    plan = await facade.plan_get(PlanIdParams(plan_id=plan_id))
    assert plan.title == "HTTP plan"
    assert len(plan.nodes) == 1
    claimed = await asyncio.to_thread(mcp.tool, "node_claim", {"node_id": str(plan.nodes[0].id)})
    assert claimed["fence"] == 1
    for _ in range(250):
        snapshot = client.store.snapshot
        if snapshot is not None and any(
            item.id == plan_id and item.nodes[0].status is NodeStatus.IN_PROGRESS for item in snapshot.plans
        ):
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail("MCP claim never reached the subscribed UI store")
    listed = await facade.plan_list(PlanListParams(project_id=project.id))
    assert [item.id for item in listed.plans] == [plan_id]
    return plan_id


async def _assert_plan_survives_restart(tmp_path: Path, plan_id: PlanId) -> None:
    daemon = _daemon(tmp_path, serve_ingest=False)
    serving = asyncio.create_task(daemon.serve())
    await wait_for_socket(daemon.socket)
    client = CoreClient(str(daemon.socket))
    try:
        await client.connect()
        restored = await TypedFacade(client).plan_get(PlanIdParams(plan_id=plan_id))
        assert restored.title == "HTTP plan"
        # A real persisted tab retains its unexpired lease across core restarts.
        assert restored.nodes[0].status is NodeStatus.IN_PROGRESS
        assert restored.nodes[0].key == "WIRE-1"
        assert restored.nodes[0].lease_expires_at is not None
    finally:
        await client.close()
        daemon.request_stop()
        assert await asyncio.wait_for(serving, 15) == 0


@pytest.mark.parametrize("decision", [ApprovalDecision.ALLOW, ApprovalDecision.DENY])
def test_tab_hook_decision_and_http_mcp_plan_reach_the_ui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    decision: ApprovalDecision,
) -> None:
    repo = _git_repo(tmp_path / "repo")
    home = tmp_path / "agent-home"
    home.mkdir()
    monkeypatch.setenv("FAKE_HOME", str(home))

    async def scenario() -> None:
        daemon = _daemon(tmp_path)
        serving = asyncio.create_task(daemon.serve())
        await wait_for_socket(daemon.socket)
        client = CoreClient(str(daemon.socket))
        hook: asyncio.Task[tuple[int, bytes]] | None = None
        try:
            await client.connect()
            facade = TypedFacade(client)
            project = await facade.project_add(ProjectAddParams(root=str(repo)))
            tab = await facade.tab_open(_open(project.id))
            spec = await facade.tab_launch_spec(TabIdParams(tab_id=tab.id))
            await client.pull_snapshot()
            assert client.store.snapshot is not None
            assert tab.id in {item.id for item in client.store.snapshot.tabs}
            assert daemon.ingest_port is not None
            token = spec.env["ACONSOLE_TAB_TOKEN"]
            body = msgspec.json.encode(
                {
                    "hook_event_name": "PermissionRequest",
                    "session_id": "application-wire",
                    "tool_name": "Bash",
                    "tool_input": {"command": "git status"},
                }
            )
            hook = asyncio.create_task(
                asyncio.to_thread(
                    _post_daemon_permission_hook,
                    daemon.ingest_port,
                    token,
                    str(tab.id),
                    body,
                )
            )
            approval = await _held(client)
            assert approval.tab_id == tab.id
            assert not hook.done(), "HTTP hook must remain blocked until the UI answers"
            applied = await ApprovalsViewModel(client).decide(approval, decision, ApprovalScope.ONCE)
            assert applied.applied
            status, reply = await asyncio.wait_for(hook, 5)
            assert status == 200
            behavior = "allow" if decision is ApprovalDecision.ALLOW else "deny"
            assert msgspec.json.decode(reply, type=HookResponse).hook_specific_output.decision.behavior == behavior
            await _gone(client)

            plan_id = await _exercise_http_plan(client, project, _HttpMcp(daemon.ingest_port, token))
        finally:
            await client.close()
            daemon.request_stop()
            assert await asyncio.wait_for(serving, 15) == 0
            if hook is not None:
                await asyncio.wait_for(hook, 6)

        # The plan was created over HTTP, and must also survive reconstruction from SQLite.
        await _assert_plan_survives_restart(tmp_path, plan_id)

    asyncio.run(scenario())
