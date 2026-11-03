"""OpenCode and Cursor CLI: the launch, the status they report, and what is refused.

The real CLIs are not used. Event shapes follow OpenCode's documentation, not a recorded stream.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import msgspec
import pytest

from aconsole.contracts.common import AgentKind
from aconsole.core.domain.agent import CAPABILITIES
from aconsole.core.domain.agent import AgentKind as DomainAgent
from aconsole.core.infrastructure.agents.launch import HostAgentCommands
from aconsole.core.infrastructure.agents.opencode.launcher import OpenCodeLauncher
from aconsole.core.infrastructure.agents.opencode.stream import EventReader, EventStream
from aconsole.core.infrastructure.containers.podman import ContainerError, Inspected
from aconsole.core.infrastructure.http.consumer import OPENCODE_EVENT, hook_event, submitted_prompt
from aconsole.core.infrastructure.http.routes import IngestItem
from aconsole.core.infrastructure.setup_checks import _agent_command  # pyright: ignore[reportPrivateUsage]
from aconsole.core.infrastructure.tabs import HostAgents

_TOKEN = "tok" + "-secret"


def _event(kind: str, **properties: object) -> bytes:
    return json.dumps({"type": kind, "properties": properties}).encode()


def _names(reader: EventReader, *events: bytes) -> list[str]:
    bodies = (reader.read(event) for event in events)
    return [json.loads(body)["hook_event_name"] for body in bodies if body is not None]


def test_capabilities_match_the_plan() -> None:
    for kind in (DomainAgent.OPENCODE, DomainAgent.CURSOR):
        names = CAPABILITIES[kind].names()
        assert {"resume", "mcp_http"} <= names
        assert not {"fork", "limits", "hooks"} & names


def test_reader_maps_a_turn_with_an_approval() -> None:
    names = _names(
        EventReader(),
        _event("server.connected"),
        _event("session.status", sessionID="s1", status={"type": "busy"}),
        _event("session.status", sessionID="s1", status={"type": "busy"}),
        _event("permission.asked", id="p1", sessionID="s1"),
        _event("session.status", sessionID="s1", status={"type": "busy"}),
        _event("permission.replied", requestID="p1", sessionID="s1", reply="once"),
        _event("session.idle", sessionID="s1"),
        _event("session.error", sessionID="s1"),
    )
    assert names == ["UserPromptSubmit", "PermissionRequest", "PreToolUse", "agent-turn-complete", "StopFailure"]


def test_reader_ignores_a_subagent_finishing_but_not_its_question() -> None:
    reader = EventReader()
    names = _names(
        reader,
        _event("session.status", sessionID="root", status={"type": "busy"}),
        _event("session.created", info={"id": "kid", "parentID": "root"}),
        _event("session.idle", sessionID="kid"),
        _event("permission.asked", id="p", sessionID="kid"),
    )
    assert names == ["UserPromptSubmit", "PermissionRequest"]


def test_reader_names_the_session_only_of_the_root() -> None:
    reader = EventReader()
    _ = reader.read(_event("session.created", info={"id": "kid", "parentID": "root"}))
    assert reader.read(_event("session.status", sessionID="kid", status={"type": "busy"})) is None
    body = reader.read(_event("session.status", sessionID="root", status={"type": "busy"}))
    assert body is not None
    assert json.loads(body)["session_id"] == "root"


@pytest.mark.parametrize("junk", [b"", b"not json", b"[]", b'{"type":"session.status","properties":{"status":7}}'])
def test_reader_survives_what_it_cannot_read(junk: bytes) -> None:
    assert EventReader().read(junk) is None


def test_consumer_reads_the_event_without_starting_a_prompt() -> None:
    body = msgspec.json.encode({"hook_event_name": "UserPromptSubmit", "session_id": "s"})
    item = IngestItem(tab_id="t", kind=OPENCODE_EVENT, body=body)
    assert hook_event(item) == "UserPromptSubmit"
    assert submitted_prompt(item) is None


def test_stream_reads_events_with_basic_auth_until_stopped() -> None:
    seen: list[str] = []
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            seen.append(self.headers.get("Authorization", ""))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for payload in (_event("server.connected"), _event("session.idle", sessionID="s")):
                frame = b"data: " + payload + b"\n\n"
                self.wfile.write(f"{len(frame):x}\r\n".encode() + frame + b"\r\n")
                self.wfile.flush()
            _ = done.wait(5)
            self.close_connection = True

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 -- the base class's name
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    bodies: list[bytes] = []
    arrived = threading.Event()

    def heard(body: bytes) -> None:
        bodies.append(body)
        arrived.set()

    stream = EventStream(server.server_address[1], _TOKEN, heard)
    stream.start()
    try:
        assert arrived.wait(5)
    finally:
        stream.stop()
        done.set()
        server.shutdown()
        server.server_close()
    assert json.loads(bodies[0])["hook_event_name"] == "agent-turn-complete"
    assert seen[0].startswith("Basic ")


def _fake_bin(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_opencode_script_starts_the_server_attaches_and_stops_it(tmp_path: Path) -> None:
    log = tmp_path / "log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_bin(bin_dir, "curl", "exit 0")
    _fake_bin(
        bin_dir,
        "opencode",
        f"""case "$1" in
serve) echo "serve $* pw=$OPENCODE_SERVER_PASSWORD" >> {log}; printf %s "$OPENCODE_CONFIG_CONTENT" > {tmp_path}/config
       echo $$ > {tmp_path}/server; exec sleep 30 ;;
attach) i=0; while [ ! -s {tmp_path}/server ] && [ $i -lt 100 ]; do sleep 0.05; i=$((i+1)); done
        echo "attach $*" >> {log} ;;
esac""",
    )
    launch = OpenCodeLauncher().launch(8765)
    assert _TOKEN not in " ".join(launch.argv)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "ACONSOLE_TAB_TOKEN": _TOKEN}
    done = subprocess.run((*launch.argv, "-s", "ses_1"), env=env, check=False, timeout=20)
    assert done.returncode == 0
    lines = log.read_text(encoding="utf-8").splitlines()
    port = launch.server_port
    assert f"serve serve --hostname 127.0.0.1 --port {port} pw={_TOKEN}" in lines
    assert f"attach attach http://127.0.0.1:{port} -s ses_1" in lines
    config = json.loads((tmp_path / "config").read_text(encoding="utf-8"))
    server = config["mcp"]["aconsole"]
    assert server["url"] == "http://127.0.0.1:8765/mcp"
    assert server["type"] == "remote"
    assert server["headers"]["Authorization"] == f"Bearer {_TOKEN}"
    pid = int((tmp_path / "server").read_text(encoding="utf-8"))
    for _ in range(50):
        if not Path(f"/proc/{pid}").exists() or "Z" in Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[2]:
            break
        _ = subprocess.run(("sleep", "0.1"), check=True)
    else:
        os.kill(pid, 9)
        pytest.fail("the OpenCode server outlived its terminal")


def test_opencode_script_says_when_it_is_not_installed(tmp_path: Path) -> None:
    launch = OpenCodeLauncher().launch(8765)
    env = {"PATH": str(tmp_path), "ACONSOLE_TAB_TOKEN": _TOKEN}
    done = subprocess.run(launch.argv, env=env, check=False, capture_output=True, timeout=10)
    assert done.returncode == 127
    assert b"opencode is not installed" in done.stderr


def test_commands_start_and_stop_one_stream_per_tab() -> None:
    commands = HostAgentCommands(lambda _name: None, lambda _tab, _body: None)
    first = commands.command(AgentKind.OPENCODE, "t1", _TOKEN, "agent-opencode", "8765")
    assert first is not None
    assert first[0] == "/bin/sh"
    assert set(commands._streams) == {"t1"}
    commands.release(AgentKind.OPENCODE, "t1", "agent-opencode")
    assert commands._streams == {}


def test_host_agents_lists_all_four() -> None:
    def inspect(name: str) -> Inspected:
        raise ContainerError(name)

    infos = HostAgents(inspect).agents()
    by_kind = {info.kind: info for info in infos}
    assert by_kind[AgentKind.OPENCODE].container == "agent-opencode"
    assert by_kind[AgentKind.OPENCODE].display_name == "OpenCode"
    assert by_kind[AgentKind.CURSOR].container == "agent-cursor"
    assert by_kind[AgentKind.CURSOR].available is False
    assert "fork" not in by_kind[AgentKind.CURSOR].capabilities
    assert "fork" in by_kind[AgentKind.CLAUDE].capabilities


def test_setup_check_asks_the_right_binary() -> None:
    assert _agent_command("agent-opencode") == "opencode"
    assert _agent_command("agent-cursor") == "agent"
