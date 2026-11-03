"""OpenCode and Cursor CLI: the launch, the status they report, and what is refused.

The real CLIs are not used. Event shapes follow OpenCode's documentation, not a recorded stream.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import msgspec
import pytest

from aconsole.contracts.app import AgentInfo  # noqa: TC001 -- only a return type, like the other imports here
from aconsole.contracts.common import AgentKind
from aconsole.core.bootstrap import accounts_without_limits, codex_poll_delay
from aconsole.core.domain.agent import CAPABILITIES
from aconsole.core.domain.agent import AgentKind as DomainAgent
from aconsole.core.infrastructure.agents.command_files import cursor_rules, opencode_commands
from aconsole.core.infrastructure.agents.cursor.launcher import CursorLauncher
from aconsole.core.infrastructure.agents.cursor.limits import CursorLimits, access_token, auth_file
from aconsole.core.infrastructure.agents.launch import HostAgentCommands
from aconsole.core.infrastructure.agents.opencode.launcher import OpenCodeLauncher
from aconsole.core.infrastructure.agents.opencode.limits import OpenCodeLimits
from aconsole.core.infrastructure.agents.opencode.limits import access_key as opencode_key
from aconsole.core.infrastructure.agents.opencode.limits import auth_file as opencode_auth
from aconsole.core.infrastructure.agents.opencode.sessions import parse_sessions
from aconsole.core.infrastructure.agents.opencode.stream import EventReader, EventStream
from aconsole.core.infrastructure.containers.podman import ContainerError, Inspected
from aconsole.core.infrastructure.http.consumer import OPENCODE_EVENT, hook_event, submitted_prompt
from aconsole.core.infrastructure.http.routes import IngestItem
from aconsole.core.infrastructure.setup_checks import _agent_command  # pyright: ignore[reportPrivateUsage]
from aconsole.core.infrastructure.tabs import HostAgents
from aconsole.sidecar.screen import ACTIVE, QUIET, SUBMITTED, ScreenWatch, visible_tail

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
        assert not {"fork", "hooks"} & names
    assert "limits" in CAPABILITIES[DomainAgent.CURSOR].names()


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


def test_cursor_entry_keeps_other_servers_and_holds_no_secret(tmp_path: Path) -> None:
    folder = tmp_path / ".cursor"
    folder.mkdir()
    path = folder / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"mine": {"url": "http://x"}}, "other": 1}), encoding="utf-8")
    assert CursorLauncher().launch(tmp_path) == ("agent",)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["other"] == 1
    assert document["mcpServers"]["mine"] == {"url": "http://x"}
    entry = document["mcpServers"]["aconsole"]
    assert entry["url"] == "http://127.0.0.1:${env:ACONSOLE_PORT}/mcp"
    assert entry["headers"]["Authorization"] == "Bearer ${env:ACONSOLE_TAB_TOKEN}"
    assert _TOKEN not in path.read_text(encoding="utf-8")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_cursor_leaves_a_file_it_cannot_read_alone(tmp_path: Path) -> None:
    folder = tmp_path / ".cursor"
    folder.mkdir()
    path = folder / "mcp.json"
    path.write_text("{ not json", encoding="utf-8")
    commands = HostAgentCommands(lambda _name: tmp_path)
    assert commands.command(AgentKind.CURSOR, "t1", _TOKEN, "agent-cursor", "8765") is None
    assert path.read_text(encoding="utf-8") == "{ not json"


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


def test_screen_watch_reports_quiet_once_and_activity_after() -> None:
    watch = ScreenWatch()
    assert watch.tick(0.0) is None
    assert watch.output(b"\x1b[2KRun command? [y/n]\r\n", 1.0) is None
    assert watch.tick(1.5) is None
    said = watch.tick(2.0)
    assert said == (QUIET, "Run command? [y/n]")
    assert watch.tick(5.0) is None
    assert watch.output(b"more", 6.0) == (ACTIVE, None)


def test_screen_watch_forgets_what_was_answered() -> None:
    watch = ScreenWatch()
    _ = watch.output(b"Allow? [y/n]", 0.0)
    assert watch.input(b"y") is None
    assert watch.input(b"text\r") == (SUBMITTED, None)
    _ = watch.output(b"done", 1.0)
    assert watch.tick(2.0) == (QUIET, "done")


def test_visible_tail_removes_controls_and_keeps_the_last_lines() -> None:
    raw = b"".join(b"line %d\r\n" % number for number in range(30)) + b"\x1b]0;title\x07\x1b[31mred\x1b[0m\x07"
    tail = visible_tail(raw).splitlines()
    assert tail[-1] == "red"
    assert len(tail) == 12


def test_opencode_sessions_skip_subagents_and_other_projects() -> None:
    rows = [
        {"id": "a", "directory": "/p/", "title": "first\nmore", "time": {"created": 1000, "updated": 2000}},
        {"id": "b", "directory": "/p", "title": "", "time": {"created": 5000, "updated": 6000}},
        {"id": "kid", "directory": "/p", "parentID": "a", "time": {"created": 1, "updated": 1}},
        {"id": "far", "directory": "/q", "time": {"created": 1, "updated": 1}},
    ]
    found = parse_sessions(json.dumps(rows).encode(), "/p").sessions
    assert [info.session_id for info in found] == ["b", "a"]
    assert found[0].title == "b"
    assert found[1].title == "first"
    assert parse_sessions(b"nope", "/p").sessions == ()


def test_command_files_find_commands_and_rules(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    (project / ".opencode" / "commands").mkdir(parents=True)
    ship = project / ".opencode" / "commands" / "ship.md"
    ship.write_text("---\ndescription: Ship it\n---\nbody", encoding="utf-8")
    (project / ".cursor" / "rules").mkdir(parents=True)
    (project / ".cursor" / "rules" / "style.mdc").write_text("no front matter", encoding="utf-8")
    home.mkdir()
    commands = opencode_commands(home, project)
    assert [(item.title, item.subtitle) for item in commands] == [("ship", "Ship it")]
    assert [(item.title, item.subtitle) for item in cursor_rules(project)] == [("style", None)]


def _info(kind: AgentKind, *, available: bool = True) -> AgentInfo:
    return next(
        msgspec.structs.replace(info, available=available)
        for info in HostAgents(lambda name: Inspected(name=name, ident="x", running=True)).agents()
        if info.kind is kind
    )


def test_an_agent_without_limits_gets_a_card_saying_they_are_unknown() -> None:
    agents = (
        _info(AgentKind.CLAUDE),
        _info(AgentKind.CODEX),
        _info(AgentKind.CURSOR),
        _info(AgentKind.OPENCODE, available=False),
    )
    found = accounts_without_limits(agents)
    assert [account.agent for account in found] == [AgentKind.CURSOR]
    assert found[0].quota == ()


def test_the_cursor_card_gives_way_to_its_numbers() -> None:
    agents = (_info(AgentKind.CURSOR), _info(AgentKind.OPENCODE))
    heard = CursorLimits(lambda: datetime(2026, 11, 3, tzinfo=UTC)).on_usage(_CURSOR_USAGE, b"")
    assert heard is not None
    found = accounts_without_limits(agents, (heard.account,))
    assert [account.agent for account in found] == [AgentKind.OPENCODE]
    # The same id: the UI replaces the card that said "unknown" instead of showing two.
    assert heard.account.id == accounts_without_limits(agents)[0].id


_CURSOR_USAGE = json.dumps({
    "billingCycleStart": "1793491200000",
    "billingCycleEnd": "1796083200000",
    "planUsage": {"totalSpend": 1234, "limit": 2000, "totalPercentUsed": 61.7},
}).encode()


def test_cursor_usage_becomes_a_monthly_window() -> None:
    now = datetime(2026, 11, 3, tzinfo=UTC)
    plan = json.dumps({"planInfo": {"planName": "Pro"}}).encode()
    event = CursorLimits(lambda: now).on_usage(_CURSOR_USAGE, plan)
    assert event is not None
    assert (event.account.agent, event.account.plan_label) == (AgentKind.CURSOR, "Pro")
    (window,) = event.account.quota
    assert (window.key, window.used_pct, window.observed_at) == ("month", 61.7, now)
    assert window.resets_at == datetime(2026, 12, 1, tzinfo=UTC)
    # The monthly allowance is a share of the plan, not money.
    assert (window.spent_usd, window.limit_usd) == (None, None)


def test_cursor_on_demand_pool_is_a_budget_in_dollars() -> None:
    now = datetime(2026, 11, 3, tzinfo=UTC)
    personal = json.loads(_CURSOR_USAGE)
    personal["spendLimitUsage"] = {"individualUsed": 1400, "individualLimit": 5000, "limitType": "user"}
    event = CursorLimits(lambda: now).on_usage(json.dumps(personal).encode(), b"")
    assert event is not None
    month, pool = event.account.quota
    assert (pool.key, pool.used_pct, pool.spent_usd, pool.limit_usd) == ("budget", 28.0, 14.0, 50.0)
    assert pool.resets_at == month.resets_at
    # A Business seat sends the team's pool, as decimal strings.
    team = json.loads(_CURSOR_USAGE)
    team["spendLimitUsage"] = {"pooledUsed": "2500", "pooledLimit": "10000", "pooledRemaining": "7500"}
    event = CursorLimits(lambda: now).on_usage(json.dumps(team).encode(), b"")
    assert event is not None
    assert event.account.quota[1].spent_usd == 25.0
    assert event.account.quota[1].used_pct == 25.0
    # No on-demand cap and nothing spent: no pool window at all.
    plain = json.loads(_CURSOR_USAGE)
    plain["spendLimitUsage"] = {"limitType": "user"}
    event = CursorLimits(lambda: now).on_usage(json.dumps(plain).encode(), b"")
    assert event is not None
    assert [window.key for window in event.account.quota] == ["month"]


_OPENCODE_NOW = datetime(2026, 11, 3, 12, tzinfo=UTC)


def test_opencode_go_counts_five_hours_a_week_and_a_month() -> None:
    body = json.dumps({
        "usage": {
            "rolling": {"percent": 35, "resetsAt": "2026-11-03T15:00:00Z"},
            "weekly": {"percent": 52.5, "resetsAt": 1_793_491_200_000},
            "monthly": {"status": "rate-limited", "percent": 99, "resetInSec": 3600},
        },
    }).encode()
    event = OpenCodeLimits(lambda: _OPENCODE_NOW).on_usage(body)
    assert event is not None
    assert (event.account.agent, event.account.id, event.account.plan_label) == (
        AgentKind.OPENCODE, "opencode:local", "Go",
    )
    rolling, week, month = event.account.quota
    assert (rolling.key, rolling.used_pct, rolling.resets_at) == ("5h", 35.0, datetime(2026, 11, 3, 15, tzinfo=UTC))
    assert (week.key, week.used_pct, week.resets_at) == ("week", 52.5, datetime(2026, 11, 1, tzinfo=UTC))
    assert (month.key, month.used_pct) == ("month", 100.0), "a rate-limited window is full"
    assert month.resets_at == datetime(2026, 11, 3, 13, tzinfo=UTC)


def test_opencode_early_shape_and_refusals() -> None:
    early = json.dumps({"rollingUsage": {"usagePercent": 10, "resetInSec": 60}}).encode()
    event = OpenCodeLimits(lambda: _OPENCODE_NOW).on_usage(early)
    assert event is not None
    assert [window.key for window in event.account.quota] == ["5h"]
    refusal = b'{"error": {"type": "EntitlementError", "message": "OpenCode Go subscription required"}}'
    for body in (b"", b"not json", refusal, b'{"status": 401}'):
        assert OpenCodeLimits(lambda: _OPENCODE_NOW).on_usage(body) is None


def test_opencode_key_is_read_from_the_cli_login(tmp_path: Path) -> None:
    assert opencode_key(tmp_path) is None
    opencode_auth(tmp_path).parent.mkdir(parents=True)
    _ = opencode_auth(tmp_path).write_text('{"anthropic": {"type": "oauth", "access": "x"}}')
    assert opencode_key(tmp_path) is None
    _ = opencode_auth(tmp_path).write_text('{"opencode": {"type": "api", "key": "zen"}, "opencode-go": {"key": "go"}}')
    assert opencode_key(tmp_path) == "go"


def test_opencode_and_cursor_now_report_limits() -> None:
    assert "limits" in CAPABILITIES[DomainAgent.OPENCODE].names()
    assert "limits" in CAPABILITIES[DomainAgent.CURSOR].names()


@pytest.mark.parametrize("body", [b"", b"{}", b'{"planUsage": {}}', b"not json", b'{"code":"unauthenticated"}'])
def test_cursor_reply_without_usage_gives_nothing(body: bytes) -> None:
    assert CursorLimits(lambda: datetime(2026, 11, 3, tzinfo=UTC)).on_usage(body, b"") is None


def test_cursor_token_is_read_from_the_cli_login(tmp_path: Path) -> None:
    assert access_token(tmp_path) is None
    auth_file(tmp_path).parent.mkdir(parents=True)
    _ = auth_file(tmp_path).write_text('{"accessToken": "t", "refreshToken": "r"}')
    assert access_token(tmp_path) == "t"
    _ = auth_file(tmp_path).write_text("[]")
    assert access_token(tmp_path) is None


def test_codex_limits_are_asked_again_soon_after_silence() -> None:
    assert codex_poll_delay(b"") < codex_poll_delay(b'{"id":2}')
