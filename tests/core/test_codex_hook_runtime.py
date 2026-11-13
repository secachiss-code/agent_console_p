"""Only the generated Codex hooks may gain session trust."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from aconsole.contracts.app import ProjectAddParams, ProjectView, TabIdParams, TabView, TerminalLaunchSpec
from aconsole.contracts.common import AgentKind
from aconsole.contracts.events import Notify
from aconsole.core.infrastructure.agents.codex.launcher import CodexLauncher, CodexTab, _hook_command
from aconsole.core.infrastructure.agents.launch import HostAgentCommands
from aconsole.ui.client.core_client import CoreClient
from tests.core.conftest import wait_for_socket
from tests.core.test_wire import _FAKE_PODMAN, _daemon, _git_repo, _open

_FIXTURES = Path(__file__).parents[1] / "fixtures/codex/0.164.0"
_TOKEN = "secret" + "-token"
_COMMAND = "/home/example/.containers/containers/agent-codex/.aconsole/bin/aconsole-hook codex"


def _reply(home: Path) -> bytes:
    doc = json.loads((_FIXTURES / "hooks-list.jsonl").read_bytes())
    for hook in doc["result"]["data"][0]["hooks"]:
        hook["command"] = _hook_command(home / ".aconsole/bin/aconsole-hook")
        if hook["eventName"] == "preToolUse":
            hook["timeoutSec"] = 600
    return json.dumps(doc).encode()


def test_readme_matches_the_codex_capability_steps() -> None:
    """The advertised Codex set starts without hooks, and the README says the same two steps."""
    from aconsole.core.domain.agent import CAPABILITIES
    from aconsole.core.domain.agent import AgentKind as DomainKind

    readme = Path(__file__).parents[2].joinpath("README.md").read_text(encoding="utf-8")
    assert not {"hooks", "approvals", "checkpoints"} & CAPABILITIES[DomainKind.CODEX].names()
    assert "семь собственных hooks" in readme
    assert "остаются в терминале" in readme
    assert "открыть `/hooks`" in readme


def test_runtime_probes_generated_hooks_before_enabling_them(tmp_path: Path) -> None:
    calls = []

    def probe(container: str, argv: tuple[str, ...]) -> bytes:
        calls.append((container, argv))
        assert sum(item.startswith("hooks.") for item in argv) == 7 + (len(calls) - 1)
        assert any(item.startswith("hooks.state=") for item in argv) is (len(calls) == 2)
        assert "secret-token" not in " ".join(argv)
        return _reply(tmp_path).replace(b'"untrusted"', b'"trusted"') if len(calls) == 2 else _reply(tmp_path)

    commands = HostAgentCommands(lambda _name: tmp_path, codex_hooks=probe)
    assert not {"hooks", "approvals", "checkpoints"} & commands.capabilities(AgentKind.CODEX)
    argv = commands.command(AgentKind.CODEX, "tab-a", "secret-token", "agent-codex", "8765")
    assert argv is not None
    assert len(calls) == 2
    assert any(item.startswith("hooks.state=") for item in argv)
    assert {"hooks", "approvals", "checkpoints"} <= commands.capabilities(AgentKind.CODEX)


@pytest.mark.parametrize("damage", ["command", "eventName", "timeoutSec", "enabled", "missing", "duplicate"])
def test_runtime_refuses_a_mismatched_hook_definition(tmp_path: Path, damage: str) -> None:
    doc = json.loads(_reply(tmp_path))
    hooks = doc["result"]["data"][0]["hooks"]
    if damage == "missing":
        hooks.pop()
    elif damage == "duplicate":
        hooks.append(hooks[0])
    else:
        invalid = {"command": str(tmp_path / "foreign"), "eventName": "stop", "timeoutSec": 999, "enabled": False}
        hooks[0][damage] = invalid[damage]
    notices = []
    commands = HostAgentCommands(
        lambda _name: tmp_path, codex_hooks=lambda _container, _argv: json.dumps(doc).encode(),
        codex_issue=lambda tab, body: notices.append((tab, body)),
    )
    argv = commands.command(AgentKind.CODEX, "tab-a", "secret-token", "agent-codex", "8765")
    assert argv is not None
    assert not any(item.startswith("hooks.") for item in argv)
    assert not {"hooks", "approvals", "checkpoints"} & commands.capabilities(AgentKind.CODEX)
    assert notices
    assert notices[0][0] == "tab-a"
    assert notices[0][1] == "codex.hooks.help"


def test_probe_does_not_change_user_configuration(tmp_path: Path) -> None:
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text("keep = true\n", encoding="utf-8")
    tab = CodexTab(agent_home=tmp_path, tab_id="tab-a", token=_TOKEN, port=8765)
    probe = CodexLauncher().probe(tab)
    assert "hooks.state=" not in " ".join(probe.argv)
    assert config.read_text(encoding="utf-8") == "keep = true\n"


@pytest.mark.parametrize("version", ["0.164.0", "0.166.0"])
def test_historical_wire_shapes_accept_the_new_hook_definitions(tmp_path: Path, version: str) -> None:
    folder = _FIXTURES.parent / version
    first = "hooks-list.jsonl" if version == "0.164.0" else "hooks-list-untrusted.jsonl"
    untrusted = json.loads((folder / first).read_bytes())
    trusted = json.loads((folder / "hooks-list-trusted.jsonl").read_bytes())
    command = _hook_command(tmp_path / ".aconsole/bin/aconsole-hook")
    for document in (untrusted, trusted):
        for hook in document["result"]["data"][0]["hooks"]:
            hook["command"] = command
            if hook["eventName"] == "preToolUse":
                hook["timeoutSec"] = 600
    launcher = CodexLauncher()
    tab = CodexTab(agent_home=tmp_path, tab_id="tab-a", token=_TOKEN, port=8765)
    launch = launcher.verified(tab, json.dumps(untrusted).encode())
    final = launcher.verified(tab, json.dumps(trusted).encode(), require_trusted=True)
    assert launch.argv == final.argv
    with pytest.raises(ValueError, match="trust did not match"):
        _ = launcher.verified(tab, json.dumps(untrusted).encode(), require_trusted=True)


@pytest.mark.parametrize("successful", [True, False])
def test_real_daemon_launch_reports_capabilities_and_fallback_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, successful: bool,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FAKE_HOME", str(home))
    fallback = tmp_path / "podman-fallback"
    fallback.write_text(_FAKE_PODMAN, encoding="utf-8")
    fallback.chmod(0o700)
    if successful:
        # The fixture acts as the container CLI. The test still uses production
        # HostTabs.codex_hooks, subprocess stdio, bootstrap and the public socket.
        podman = tmp_path / "podman"
        reply = _reply(home).decode()
        script = (
            f"#!{sys.executable}\nimport os,sys\na=sys.argv[1:]\n"
            "if 'app-server' in a and any(s.startswith('hooks.SessionStart=') for s in a):\n"
            " sys.stdin.readline()\n"
            f" body={reply!r}\n"
            " if any(s.startswith('hooks.state=') for s in a): body=body.replace('untrusted','trusted')\n"
            " print(body,flush=True)\n"
            f"else: os.execv({str(fallback)!r},[{str(fallback)!r},*a])\n"
        )
        podman.write_text(script, encoding="utf-8")
        podman.chmod(0o700)
    repo = _git_repo(tmp_path / "repo")

    async def scenario() -> None:
        import msgspec

        daemon = _daemon(tmp_path)
        serving = asyncio.create_task(daemon.serve())
        await wait_for_socket(daemon.socket)
        client = CoreClient(str(daemon.socket))
        notices = []
        client.store.subscribe(lambda event, _store: notices.append(event) if isinstance(event, Notify) else None)
        try:
            _ = await client.connect()
            before, _ = await client.pull_snapshot()
            initial = next(agent for agent in before.snapshot.agents if agent.kind is AgentKind.CODEX)
            assert not {"hooks", "approvals", "checkpoints"} & initial.capabilities
            project = await client.request("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            params = msgspec.structs.replace(_open(project.id), agent=AgentKind.CODEX)
            tab = await client.request("tab/open", params, TabView)
            spec = await client.request("tab/launchSpec", TabIdParams(tab_id=tab.id), TerminalLaunchSpec)
            after, _ = await client.pull_snapshot()
            agent = next(agent for agent in after.snapshot.agents if agent.kind is AgentKind.CODEX)
            enabled = {"hooks", "approvals", "checkpoints"}
            assert (enabled <= agent.capabilities) is successful
            assert any(arg.startswith("hooks.state=") for arg in spec.argv) is successful
            if not successful:
                assert notices
                assert notices[0].tab_id == tab.id
                assert notices[0].title == "notify.codex_hooks_unavailable"
                assert notices[0].body == "codex.hooks.help"
        finally:
            await client.close()
            daemon.request_stop()
            assert await serving == 0

    asyncio.run(scenario())
