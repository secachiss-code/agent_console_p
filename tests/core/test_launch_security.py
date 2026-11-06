"""Launch secrets, hostile session identifiers and local HTTP admission boundaries."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import msgspec
import pytest

from aconsole.contracts.common import AgentKind
from aconsole.core.application.services.tabs import TabRequestError, _launch_command
from aconsole.core.infrastructure.agents.claude.launcher import ClaudeLauncher, ClaudeTab
from aconsole.core.infrastructure.agents.codex.launcher import CodexLauncher, CodexTab
from aconsole.core.infrastructure.agents.cursor.launcher import CursorLauncher
from aconsole.core.infrastructure.containers.podman import ExecRequest, Podman
from aconsole.core.infrastructure.mcp.server import TabTokens, beside_ingest

_SECRET = "test-private-" + "token-123"


def test_podman_inherits_the_token_by_name_without_exposing_its_value() -> None:
    argv = Podman(Path("/usr/bin/podman")).exec_spec(ExecRequest(name="box", command=("true",), env={
        "ACONSOLE_TAB_TOKEN": _SECRET, "ACONSOLE_TAB": "tab", "PATH": "/bin",
    }, workdir="/work", user="sas"))
    assert _SECRET not in " ".join(argv)
    assert "ACONSOLE_TAB_TOKEN" in argv
    assert "ACONSOLE_TAB=tab" in argv


@pytest.mark.parametrize("agent", list(AgentKind))
@pytest.mark.parametrize("session", ["--help", "-x", "../escape", "has space", "a\nb", ""])
def test_runtime_refuses_hostile_session_ids(agent: AgentKind, session: str) -> None:
    with pytest.raises(TabRequestError):
        _ = _launch_command(agent, session, fork=False)


def test_codex_positional_session_has_an_option_terminator() -> None:
    assert _launch_command(AgentKind.CODEX, "safe-session", fork=False) == ("codex", "resume", "--", "safe-session")


@pytest.mark.parametrize("agent", ["claude", "codex", "cursor"])
def test_launchers_refuse_a_symlink_in_the_home_ancestors(tmp_path: Path, agent: str) -> None:
    outside = tmp_path / "outside"
    home = outside / "home"
    home.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(outside, target_is_directory=True)
    def launch() -> None:
        if agent == "claude":
            _ = ClaudeLauncher().launch(ClaudeTab(agent_home=alias / "home", tab_id="tab", port=8765, token=_SECRET))
        elif agent == "codex":
            _ = CodexLauncher().probe(CodexTab(agent_home=alias / "home", tab_id="tab", port=8765, token=_SECRET))
        else:
            _ = CursorLauncher().launch(alias / "home")

    with pytest.raises((ValueError, OSError)):
        launch()
    assert list(home.iterdir()) == []


@pytest.mark.parametrize("headers", [
    [(b"host", b"evil.example")],
    [(b"host", b"127.0.0.1.evil.example")],
    [(b"host", b"localhost@evil.example")],
    [(b"host", b"[broken")],
    [(b"origin", b"https://evil.example")],
    [(b"origin", b"null"), (b"origin", b"http://evil.example")],
    [(b"host", b"localhost"), (b"host", b"evil.example")],
])
def test_mcp_refuses_foreign_or_ambiguous_origin_and_host(headers: list[tuple[bytes, bytes]]) -> None:
    reached = []
    responses = []

    async def target(_scope: object, _receive: object, _send: object) -> None:
        reached.append(True)

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b""}

    async def send(message: object) -> None:
        responses.append(message)

    app = beside_ingest(target, target, TabTokens())
    asyncio.run(app({"type": "http", "path": "/mcp", "headers": headers}, receive, send))
    assert reached == []
    assert responses[0]["status"] == 403


def test_generated_hook_scripts_pass_auth_outside_curl_argv(tmp_path: Path) -> None:
    codex = CodexLauncher().probe(CodexTab(agent_home=tmp_path, tab_id="tab", port=8765, token=_SECRET))
    claude = ClaudeLauncher().launch(ClaudeTab(agent_home=tmp_path, tab_id="tab", port=8765, token=_SECRET))
    for path in (codex.hook_path, codex.notify_path, claude.script_path):
        text = path.read_text()
        assert '-H "Authorization: Bearer' not in text
        assert _SECRET not in text
    assert json.loads(claude.settings_path.read_text())["hooks"]



@pytest.mark.parametrize("kind", ["hook", "notify_arg", "notify_stdin", "statusline"])
def test_real_curl_receives_auth_and_body_without_secret_in_argv(tmp_path: Path, kind: str) -> None:
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            size = int(self.headers.get("Content-Length", "0"))
            received.append((self.headers.get("Authorization"), self.rfile.read(size)))
            self.send_response(200)
            self.end_headers()
            _ = self.wfile.write(b"{}")

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    capture = tmp_path / "curl-args"
    binary = tmp_path / "curl"
    binary.write_text('#!/bin/sh\nprintf \'%s\\0\' "$@" > "$CAPTURE"\nexec /usr/bin/curl "$@"\n')
    binary.chmod(0o700)
    env = {**os.environ, "PATH": f"{tmp_path}:/usr/bin:/bin", "CAPTURE": str(capture),
           "ACONSOLE_TAB_TOKEN": _SECRET, "ACONSOLE_PORT": str(port), "ACONSOLE_TAB": "tab"}
    payload = b'{"event":"probe"}'
    try:
        if kind == "statusline":
            launched = ClaudeLauncher().launch(ClaudeTab(agent_home=tmp_path, tab_id="tab", port=port, token=_SECRET))
            argv = [str(launched.script_path)]
        else:
            launched = CodexLauncher().probe(CodexTab(agent_home=tmp_path, tab_id="tab", port=port, token=_SECRET))
            argv = [str(launched.hook_path if kind == "hook" else launched.notify_path)]
            if kind == "notify_arg":
                argv.extend(("codex", payload.decode()))
        result = subprocess.run(argv, input=payload, env=env, capture_output=True, timeout=5, check=False)
        assert result.returncode == 0
        assert received == [(f"Bearer {_SECRET}", payload)]
        assert _SECRET.encode() not in capture.read_bytes()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_private_write_does_not_follow_a_parent_swapped_to_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aconsole.core.infrastructure.agents import private_files

    parent = tmp_path / "inside"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "settings"
    target.write_bytes(b"untouched")
    original = os.replace

    def swap(source: str, destination: str, *, src_dir_fd: int, dst_dir_fd: int) -> None:
        parent.rename(tmp_path / "original")
        parent.symlink_to(outside, target_is_directory=True)
        original(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(private_files.os, "replace", swap)
    private_files.atomic_write(parent / "settings", b"private", 0o600)
    assert target.read_bytes() == b"untouched"
    assert (tmp_path / "original/settings").read_bytes() == b"private"



@pytest.mark.parametrize("path", ["/mcp", "/health"])
@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"}, {"Host": "evil.example"}])
def test_serving_daemon_refuses_foreign_http_headers(tmp_path: Path, path: str, headers: dict[str, str]) -> None:
    from tests.core.test_wire import _connect, _daemon, _stop

    def request(port: int) -> None:
        query = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
        with pytest.raises(urllib.error.HTTPError) as caught:
            _ = urllib.request.urlopen(query, timeout=2)  # noqa: S310 -- fixed loopback HTTP probe
        assert caught.value.code == 403
        caught.value.close()

    async def scenario() -> None:
        daemon = _daemon(tmp_path)
        serving = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            await asyncio.to_thread(request, daemon.ingest_port)
        finally:
            await _stop(daemon, serving, client)

    asyncio.run(scenario())


@pytest.mark.parametrize("agent", [AgentKind.CLAUDE, AgentKind.CODEX, AgentKind.OPENCODE, AgentKind.CURSOR])
def test_rpc_launch_spec_keeps_ingest_and_sidecar_secrets_out_of_argv(
    tmp_path: Path, agent: AgentKind, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aconsole.contracts.app import ProjectAddParams, ProjectView, TabIdParams, TabView, TerminalLaunchSpec
    from tests.core.test_wire import _connect, _daemon, _git_repo, _open, _stop

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("FAKE_HOME", str(home))
    repo = _git_repo(tmp_path / "repo")

    async def scenario() -> None:
        daemon = _daemon(tmp_path)
        serving = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            tab = await client.call("tab/open", msgspec.structs.replace(_open(project.id), agent=agent), TabView)
            launch = await client.call("tab/launchSpec", TabIdParams(tab_id=tab.id), TerminalLaunchSpec)
            for name in ("ACONSOLE_TAB_TOKEN", "ACONSOLE_SIDECAR_TOKEN"):
                assert launch.env[name]
                assert launch.env[name] not in " ".join(launch.argv)
            assert "ACONSOLE_TAB_TOKEN" in launch.argv
            assert launch.argv[1:3] == ("-I", "-m")
        finally:
            await _stop(daemon, serving, client)

    asyncio.run(scenario())
