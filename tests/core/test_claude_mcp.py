"""Claude's final launch configuration reaches the real MCP endpoint with its tab identity."""

from __future__ import annotations

import asyncio
import http.client
import json
import secrets
import stat
from pathlib import Path
from string import Template
from urllib.parse import urlsplit

import pytest

from aconsole.contracts.app import ProjectAddParams, ProjectView, TabIdParams, TabView, TerminalLaunchSpec
from aconsole.core.infrastructure.agents.claude.launcher import ClaudeLauncher, ClaudeTab
from tests.core.test_wire import _connect, _daemon, _git_repo, _open, _stop


def _config(argv: tuple[str, ...]) -> tuple[Path, dict[str, object]]:
    assert "--mcp-config" in argv
    path = Path(argv[argv.index("--mcp-config") + 1])
    document = json.loads(path.read_text(encoding="utf-8"))
    return path, document["mcpServers"]["aconsole"]


def test_claude_mcp_is_private_and_keeps_the_secret_in_the_environment(tmp_path: Path) -> None:
    token = secrets.token_hex(16)
    launch = ClaudeLauncher().launch(ClaudeTab(agent_home=tmp_path, tab_id="tab-a", port=8765, token=token))
    path, config = _config(launch.argv)
    assert config == {
        "type": "http", "url": "http://127.0.0.1:8765/mcp",
        "headers": {"Authorization": "Bearer ${ACONSOLE_TAB_TOKEN}"},
    }
    assert path.parent == launch.settings_path.parent
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert token not in path.read_text(encoding="utf-8")
    assert token not in " ".join(launch.argv)
    assert launch.env["ACONSOLE_TAB_TOKEN"] == token
    ClaudeLauncher().close(tmp_path, "tab-a")
    assert not path.exists()


def test_claude_mcp_does_not_follow_a_destination_symlink(tmp_path: Path) -> None:
    directory = tmp_path / ".aconsole/run/tab-a"
    directory.mkdir(parents=True)
    target = tmp_path / "keep.json"
    _ = target.write_text("keep\n", encoding="utf-8")
    (directory / "mcp.json").symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        ClaudeLauncher().launch(ClaudeTab(agent_home=tmp_path, tab_id="tab-a", port=8765, token=secrets.token_hex(16)))
    assert target.read_text(encoding="utf-8") == "keep\n"


class _Mcp:
    def __init__(self, config: dict[str, object], env: dict[str, str]) -> None:
        self.url = urlsplit(str(config["url"]))
        headers = config["headers"]
        assert isinstance(headers, dict)
        self.headers = {name: Template(value).substitute(env) for name, value in headers.items()}
        self.headers.update({"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
        self.ident = 0

    def request(self, method: str, params: dict[str, object]) -> dict[str, object]:
        self.ident += 1
        connection = http.client.HTTPConnection(self.url.hostname, self.url.port, timeout=5)
        try:
            message = {"jsonrpc": "2.0", "method": method, "params": params}
            if not method.startswith("notifications/"):
                message["id"] = self.ident
            connection.request("POST", self.url.path, json.dumps(message), self.headers)
            response = connection.getresponse()
            assert response.status in {200, 202}
            if response.status == 202:
                return {}
            session = response.getheader("mcp-session-id")
            if session is not None:
                self.headers["Mcp-Session-Id"] = session
            body = json.loads(response.read())
            result = body["result"]
            if method == "initialize":
                self.headers["MCP-Protocol-Version"] = result["protocolVersion"]
            return result
        finally:
            connection.close()

    def start(self) -> None:
        _ = self.request("initialize", {
            "protocolVersion": "2026-07-28", "capabilities": {},
            "clientInfo": {"name": "claude-launch-test", "version": "1"},
        })
        _ = self.request("notifications/initialized", {})


def _content(result: dict[str, object]) -> dict[str, object]:
    return json.loads(result["content"][0]["text"])


def test_claude_launch_config_lists_tools_and_isolates_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FAKE_HOME", str(home))
    repos = (_git_repo(tmp_path / "repo-a"), _git_repo(tmp_path / "repo-b"))

    async def scenario() -> None:
        daemon = _daemon(tmp_path)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            sessions = []
            for repo in repos:
                project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
                tab = await client.call("tab/open", _open(project.id), TabView)
                spec = await client.call("tab/launchSpec", TabIdParams(tab_id=tab.id), TerminalLaunchSpec)
                _path, config = _config(spec.argv)
                session = _Mcp(config, spec.env)
                await asyncio.to_thread(session.start)
                sessions.append(session)
            tools = await asyncio.to_thread(sessions[0].request, "tools/list", {})
            assert {tool["name"] for tool in tools["tools"]} >= {
                "plan_create", "plan_add_nodes", "plan_get", "plan_ready", "node_claim", "node_update",
                "node_attach", "ctx_get", "kb_add", "kb_impact", "handover", "plan_list",
            }
            made = await asyncio.to_thread(sessions[0].request, "tools/call", {
                "name": "plan_create", "arguments": {"title": "Claude plan", "goal": "Ship"},
            })
            assert not made.get("isError", False)
            plan_id = _content(made)["plan_id"]
            own = await asyncio.to_thread(sessions[0].request, "tools/call", {
                "name": "plan_get", "arguments": {"plan_id": plan_id},
            })
            assert not own.get("isError", False)
            other = await asyncio.to_thread(sessions[1].request, "tools/call", {
                "name": "plan_get", "arguments": {"plan_id": plan_id},
            })
            assert other["isError"]
            assert "PLAN_NOT_FOUND" in str(other)
        finally:
            await _stop(daemon, task, client)

    asyncio.run(scenario())
