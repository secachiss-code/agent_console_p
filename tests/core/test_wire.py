"""The assembled daemon: a project, a real launch command, and a hook that moves the tab."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import urllib.request
from pathlib import Path

import msgspec

from aconsole.contracts.app import (
    AppSnapshot,
    EmptyParams,
    EmptyResult,
    OpenTabParams,
    ProjectAddParams,
    ProjectIdParams,
    ProjectView,
    SettingsUpdateParams,
    SnapshotResult,
    TabIdParams,
    TabView,
    TerminalLaunchSpec,
)
from aconsole.contracts.common import AgentKind, ClientRole, ProjectId, TabStatus, WorkspaceMode
from aconsole.contracts.errors import error_by_name
from aconsole.contracts.ipc import InitializeParams, ProtocolVersion, Request, ResponseErr, ResponseOk
from aconsole.contracts.settings import Settings, SettingsPatch
from aconsole.core.bootstrap import Daemon
from aconsole.core.infrastructure.ipc.framing import decode_envelope, encode_line
from aconsole.core.infrastructure.tabs import HostBins, HostTabs
from tests.core.conftest import wait_for_socket

_ENCODER = msgspec.json.Encoder()
_CODEX_REPLY = (
    '{"id":2,"result":{"rateLimits":{"planType":"plus",'
    '"primary":{"usedPercent":7,"windowDurationMins":300,"resetsAt":1900000000},'
    '"secondary":{"usedPercent":28,"windowDurationMins":10080,"resetsAt":1900000000}}}}'
)
_FAKE_PODMAN = """#!/bin/sh
case "$1" in
inspect) printf '[{"Id":"i","Name":"x","State":{"Running":true}}]\\n' ;;
exec)
  case "$*" in
  *"/usage"*) printf 'Current session: 40%% used · resets Jan 1, 1am (UTC)\\nCurrent week (all models): 12%% used\\n' ;;
  *"auth status"*) printf '{"loggedIn":true,"email":"e@x","subscriptionType":"pro"}' ;;
  *app-server*) read -r _; printf '@REPLY@\\n' ;;
  *) printf '%s' "$FAKE_HOME" ;;
  esac ;;
esac
""".replace("@REPLY@", _CODEX_REPLY)


def _git_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    _ = subprocess.run(["/usr/bin/git", "init", "-q", "-b", "trunk", str(root)], check=True)
    return root


def _daemon(tmp: Path, *, serve_ingest: bool = True) -> Daemon:
    podman = tmp / "podman"
    if not podman.exists():
        _ = podman.write_text(_FAKE_PODMAN, encoding="utf-8")
        podman.chmod(podman.stat().st_mode | stat.S_IEXEC)
    socket = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / f"ac-wire-{tmp.name[-20:]}.sock"  # noqa: S108 -- AF_UNIX limit
    host = HostTabs(bins=HostBins(podman=podman), socket_path=str(socket), user="sas", worktrees_dir=tmp / "wt")
    daemon = Daemon(socket, database=tmp / "aconsole.db", host=host, idle_s=30.0)
    daemon.serve_ingest = serve_ingest
    return daemon


class _Client:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.next_id = 1

    async def call[T: msgspec.Struct](self, method: str, params: msgspec.Struct, result: type[T]) -> T:
        reply = await self.raw(method, params)
        assert isinstance(reply, ResponseOk), reply
        return msgspec.json.decode(reply.result, type=result)

    async def error(self, method: str, params: msgspec.Struct) -> str:
        reply = await self.raw(method, params)
        assert isinstance(reply, ResponseErr), reply
        return reply.error.message

    async def raw(self, method: str, params: msgspec.Struct) -> ResponseOk | ResponseErr:
        self.next_id += 1
        ident = self.next_id
        request = Request(id=ident, method=method, params=msgspec.Raw(_ENCODER.encode(params)))
        self.writer.write(encode_line(_ENCODER, request))
        await self.writer.drain()
        while True:
            envelope = decode_envelope(await self.reader.readline())
            if isinstance(envelope, (ResponseOk, ResponseErr)) and envelope.id == ident:
                return envelope

    async def snapshot(self) -> AppSnapshot:
        return (await self.call("app/snapshot", EmptyParams(), SnapshotResult)).snapshot


async def _connect(socket: Path) -> _Client:
    await wait_for_socket(socket)
    reader, writer = await asyncio.open_unix_connection(socket)
    client = _Client(reader, writer)
    params = InitializeParams(
        protocol=(ProtocolVersion(major=1, minor=0), ProtocolVersion(major=1, minor=0)),
        role=ClientRole.UI, client_name="wire", client_version="0", pid=os.getpid(),
        capabilities=frozenset({"events.v1"}),
    )
    writer.write(encode_line(_ENCODER, Request(id=1, method="initialize", params=msgspec.Raw(_ENCODER.encode(params)))))
    await writer.drain()
    _ = await reader.readline()
    return client


async def _stop(daemon: Daemon, task: asyncio.Task[int], client: _Client) -> None:
    client.writer.close()
    daemon.request_stop()
    assert await task == 0


def _post_hook(port: int, tab_id: str, token: str, event: str) -> int:
    body = json.dumps({"hook_event_name": event, "session_id": "s", "cwd": "/w"}).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/tabs/{tab_id}/claude/hook",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as answer:  # noqa: S310 -- loopback only
        return int(answer.status)


def _post_statusline(port: int, tab_id: str, token: str) -> None:
    body = json.dumps(
        {
            "session_id": "s",
            "rate_limits": {
                "five_hour": {"used_percentage": 99, "resets_at": 1900000000},
                "seven_day": {"used_percentage": 99, "resets_at": 1900000000},
            },
        },
    ).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/tabs/{tab_id}/claude/statusline",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5):  # noqa: S310 -- loopback only
        pass


def test_limits_of_both_agents_reach_the_snapshot(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001 -- pytest fixture
    repo = _git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FAKE_HOME", str(home))

    async def scenario() -> dict[str, list[float | None]]:
        daemon = _daemon(tmp_path)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        tab = await client.call("tab/open", _open(project.id), TabView)
        spec = await client.call("tab/launchSpec", TabIdParams(tab_id=tab.id), TerminalLaunchSpec)
        port = daemon.ingest_port
        assert port is not None
        # A statusline repeats its tab's last reply: it must not move the limits.
        await asyncio.to_thread(_post_statusline, port, str(tab.id), spec.env["ACONSOLE_TAB_TOKEN"])
        found: dict[str, list[float | None]] = {}
        for _ in range(100):
            snapshot = await client.snapshot()
            found = {item.agent.value: [window.used_pct for window in item.quota] for item in snapshot.accounts}
            if found.get("claude") and found.get("codex"):
                break
            await asyncio.sleep(0.05)
        # Each card says where its numbers come from, and a refresh is answered at once.
        sources = {item.agent.value: (item.source, item.refresh_s) for item in snapshot.accounts}
        assert sources["claude"] == ("claude-usage", 60)
        assert sources["codex"] == ("codex-app-server", 120)
        assert await client.call("quota/refresh", EmptyParams(), EmptyResult) == EmptyResult()
        await _stop(daemon, task, client)
        return found

    assert asyncio.run(scenario()) == {
        "claude": [40.0, 12.0],
        "codex": [7.0, 28.0],
        "opencode": [],
        "cursor": [],
    }


def test_projects_are_added_once_activated_and_survive_a_restart(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")

    async def scenario() -> tuple[ProjectView, ProjectView, AppSnapshot, str, str]:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        first = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        again = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        not_repo = await client.error("project/add", ProjectAddParams(root=str(tmp_path)))
        relative = await client.error("project/add", ProjectAddParams(root="repo"))
        await _stop(daemon, task, client)
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        restored = await client.snapshot()
        await _stop(daemon, task, client)
        return first, again, restored, not_repo, relative

    first, again, restored, not_repo, relative = asyncio.run(scenario())
    assert first == again
    assert first.default_branch == "trunk"
    assert not_repo == error_by_name("PROJECT_NOT_A_GIT_REPO").name
    assert relative == error_by_name("PATH_NOT_ALLOWED").name
    assert restored.projects == (first,)
    assert restored.active_project == first.id
    assert [agent.kind for agent in restored.agents] == [
        AgentKind.CLAUDE,
        AgentKind.CODEX,
        AgentKind.OPENCODE,
        AgentKind.CURSOR,
    ]
    assert all(agent.available for agent in restored.agents)


def test_a_project_with_tabs_cannot_be_removed(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")

    async def scenario() -> tuple[str, str]:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        _ = await client.call("tab/open", _open(project.id), TabView)
        refused = await client.error("project/remove", ProjectIdParams(project_id=project.id))
        missing = await client.error("project/setActive", ProjectIdParams(project_id=ProjectId("nope")))
        await _stop(daemon, task, client)
        return refused, missing

    assert asyncio.run(scenario()) == ("TAB_STATE_CONFLICT", "PROJECT_NOT_FOUND")


def _open(project: ProjectId) -> OpenTabParams:
    return OpenTabParams(project_id=project, agent=AgentKind.CLAUDE, workspace=WorkspaceMode.MAIN_CHECKOUT)


def test_tab_runs_claude_with_hooks_and_a_hook_moves_it(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001 -- pytest fixture
    repo = _git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FAKE_HOME", str(home))

    async def scenario() -> tuple[TerminalLaunchSpec, TabStatus, TerminalLaunchSpec, int]:
        daemon = _daemon(tmp_path)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
        tab = await client.call("tab/open", _open(project.id), TabView)
        spec = await client.call("tab/launchSpec", TabIdParams(tab_id=tab.id), TerminalLaunchSpec)
        port = daemon.ingest_port
        assert port is not None
        token = spec.env["ACONSOLE_TAB_TOKEN"]
        code = await asyncio.to_thread(_post_hook, port, str(tab.id), token, "UserPromptSubmit")
        assert code == 200
        status = await _wait_status(client, TabStatus.IDLE)
        await _stop(daemon, task, client)
        # A restored tab has no token until launchSpec mints one, and the hooks must then be accepted.
        daemon = _daemon(tmp_path)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        restored = (await client.snapshot()).tabs[0]
        again = await client.call("tab/launchSpec", TabIdParams(tab_id=restored.id), TerminalLaunchSpec)
        port = daemon.ingest_port
        assert port is not None
        accepted = await asyncio.to_thread(_post_hook, port, str(restored.id), again.env["ACONSOLE_TAB_TOKEN"], "Stop")
        await _stop(daemon, task, client)
        return spec, status, again, accepted

    spec, status, again, accepted = asyncio.run(scenario())
    agent = spec.argv[spec.argv.index("agent-claude") + 1 :]
    assert spec.argv[spec.argv.index("--user") + 1] == "sas"
    assert "agent-claude" in spec.argv
    assert agent[:2] == ("/bin/sh", "-c")
    settings = Path(agent[agent.index("--settings") + 1])
    assert settings.is_relative_to(home)
    assert "/claude/hook" in settings.read_text(encoding="utf-8")
    assert status is TabStatus.WORKING
    assert accepted == 200
    assert again.env["ACONSOLE_TAB_TOKEN"] != spec.env["ACONSOLE_TAB_TOKEN"]


async def _wait_status(client: _Client, other_than: TabStatus) -> TabStatus:
    for _ in range(200):
        status = (await client.snapshot()).tabs[0].status
        if status is not other_than:
            return status
        await asyncio.sleep(0.02)
    return other_than


def test_preferences_survive_a_restart(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, str, str, str, int, str | None]:
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        first = await client.call("settings/get", EmptyParams(), Settings)
        patch = SettingsPatch(quota_display="remaining", theme="dark", terminal_font_size=14, terminal_font="Mono")
        changed = await client.call("settings/update", SettingsUpdateParams(patch=patch, rev=first.rev), Settings)
        await _stop(daemon, task, client)
        daemon = _daemon(tmp_path, serve_ingest=False)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        restored = await client.call("settings/get", EmptyParams(), Settings)
        await _stop(daemon, task, client)
        return (
            first.quota_display, changed.quota_display, restored.quota_display, restored.theme,
            restored.terminal_font_size, restored.terminal_font,
        )

    assert asyncio.run(scenario()) == ("used", "remaining", "remaining", "dark", 14, "Mono")
