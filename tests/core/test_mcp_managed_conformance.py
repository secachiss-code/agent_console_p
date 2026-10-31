"""The managed conformance runner owns a private real-daemon lifecycle."""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import signal
import socket
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from aconsole.devcli import mcp_conformance

_CONFORMANCE = "@modelcontextprotocol/conformance@0.2.0-alpha.12"
_REVISION = "2026-07-28"


def _prepare_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    state = root / ".aconsole-dev"
    user_state = state / "user-state"
    user_state.mkdir(parents=True)
    canary = user_state / "keep.txt"
    canary.write_text("preserve this user state", encoding="utf-8")
    default_db = root / ".local" / "share" / "aconsole" / "aconsole.db"
    default_db.parent.mkdir(parents=True)
    default_db.write_bytes(b"existing user database sentinel")
    return root, default_db


def _assert_only_owned_state_was_removed(root: Path, default_db: Path) -> None:
    state = root / ".aconsole-dev"
    assert sorted(path.name for path in state.iterdir()) == ["user-state"]
    assert (state / "user-state" / "keep.txt").read_text(encoding="utf-8") == "preserve this user state"
    assert default_db.read_bytes() == b"existing user database sentinel"


def _mcp_request(
    url: str,
    message: dict[str, object],
    *,
    session_id: str | None = None,
    protocol_version: str | None = None,
) -> tuple[int, dict[str, str], object]:
    parsed = urlsplit(url)
    assert parsed.scheme == "http"
    assert parsed.hostname == "127.0.0.1"
    assert parsed.path == "/mcp"
    assert parsed.port is not None
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if message.get("method") != "initialize":
        assert protocol_version is not None
        headers["MCP-Protocol-Version"] = protocol_version
    if session_id is not None:
        headers["Mcp-Session-Id"] = session_id
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
    try:
        connection.request("POST", parsed.path, json.dumps(message), headers)
        response = connection.getresponse()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        raw = response.read()
    finally:
        connection.close()
    body = None if raw == b"" else json.loads(raw)
    return response.status, response_headers, body


def _assert_port_closed(port: int) -> None:
    with pytest.raises(ConnectionRefusedError):
        _ = socket.create_connection(("127.0.0.1", port), timeout=0.2)


@pytest.mark.parametrize("suite_status", [0, 1, 124])
def test_run_managed_uses_a_live_loopback_endpoint_and_cleans_its_private_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suite_status: int,
) -> None:
    root, default_db = _prepare_root(tmp_path)
    calls: list[tuple[Path, str, float]] = []

    async def probe_tooling(_root: Path) -> int:
        return 0

    async def run_suite(suite_root: Path, url: str, timeout_s: float) -> int:
        calls.append((suite_root, url, timeout_s))
        status, headers, response = await asyncio.to_thread(
            _mcp_request,
            url,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2026-07-28",
                    "capabilities": {},
                    "clientInfo": {"name": "managed-conformance-test", "version": "1.0"},
                },
            },
        )
        assert status == 200
        assert isinstance(response, dict)
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        result = response["result"]
        assert isinstance(result, dict)
        # MCP_TOOLS.md distinguishes the modern 2026 route from legacy initialize negotiation.
        # Honor the server-selected version here; the pinned suite separately requires 2026-07-28.
        negotiated_version = result["protocolVersion"]
        assert isinstance(negotiated_version, str)
        assert negotiated_version
        assert isinstance(result["capabilities"], dict)
        assert isinstance(result["serverInfo"], dict)
        session_id = headers.get("mcp-session-id")
        if session_id is not None:
            _, _, initialized = await asyncio.to_thread(
                _mcp_request,
                url,
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                session_id=session_id,
                protocol_version=negotiated_version,
            )
            assert initialized is None or isinstance(initialized, dict)
        list_status, _, listed = await asyncio.to_thread(
            _mcp_request,
            url,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            session_id=session_id,
            protocol_version=negotiated_version,
        )
        assert list_status == 200
        assert isinstance(listed, dict)
        tool_result = listed["result"]
        assert isinstance(tool_result, dict)
        tools = tool_result["tools"]
        assert isinstance(tools, list)
        assert len(tools) == 12
        assert "authorization" not in headers
        return suite_status

    monkeypatch.setattr(mcp_conformance, "_probe_tooling", probe_tooling)
    monkeypatch.setattr(mcp_conformance, "_run_suite", run_suite)

    result = asyncio.run(mcp_conformance.run_managed(root=root, suite_timeout_s=12))

    assert result == suite_status
    assert len(calls) == 1
    suite_root, url, timeout_s = calls[0]
    assert suite_root == root
    assert urlsplit(url).hostname == "127.0.0.1"
    assert urlsplit(url).path == "/mcp"
    assert 1024 <= (urlsplit(url).port or 0) <= 65535
    assert timeout_s == 12
    _assert_port_closed(urlsplit(url).port or 0)
    _assert_only_owned_state_was_removed(root, default_db)


def test_run_managed_preflight_failure_does_not_start_daemon_or_touch_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, default_db = _prepare_root(tmp_path)
    suite_called = False

    async def probe_tooling(_root: Path) -> int:
        return 42

    async def run_suite(_root: Path, _url: str, _timeout_s: float) -> int:
        nonlocal suite_called
        suite_called = True
        return 0

    def daemon_must_not_start(*_args: object, **_kwargs: object) -> None:
        pytest.fail("preflight failure started the daemon")

    monkeypatch.setattr(mcp_conformance, "_probe_tooling", probe_tooling)
    monkeypatch.setattr(mcp_conformance, "_run_suite", run_suite)
    monkeypatch.setattr(mcp_conformance, "Daemon", daemon_must_not_start)

    result = asyncio.run(mcp_conformance.run_managed(root=root))

    assert result == 42
    assert suite_called is False
    _assert_only_owned_state_was_removed(root, default_db)


def test_run_managed_cancellation_stops_daemon_and_removes_only_owned_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, default_db = _prepare_root(tmp_path)
    urls: list[str] = []

    async def probe_tooling(_root: Path) -> int:
        return 0

    async def run_suite(_root: Path, _url: str, _timeout_s: float) -> int:
        urls.append(_url)
        raise asyncio.CancelledError

    monkeypatch.setattr(mcp_conformance, "_probe_tooling", probe_tooling)
    monkeypatch.setattr(mcp_conformance, "_run_suite", run_suite)

    with pytest.raises(asyncio.CancelledError):
        _ = asyncio.run(mcp_conformance.run_managed(root=root))

    assert len(urls) == 1
    assert urlsplit(urls[0]).port is not None
    _assert_port_closed(urlsplit(urls[0]).port or 0)
    _assert_only_owned_state_was_removed(root, default_db)


def test_main_rejects_arguments_without_starting_the_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    def run_managed(*, root: Path, suite_timeout_s: float = 90.0) -> int:
        assert root.is_absolute()
        assert suite_timeout_s > 0
        pytest.fail("the runner must not start when CLI arguments are unsupported")

    monkeypatch.setattr(mcp_conformance, "run_managed", run_managed)

    assert mcp_conformance.main(["unexpected"]) != 0


class _Process:
    def __init__(self, *, block: bool, exit_code: int = 0) -> None:
        self.pid = 43120
        self.returncode: int | None = None
        self.block = block
        self.exit_code = exit_code
        self.started = asyncio.Event()
        self.waited = False

    async def communicate(self) -> tuple[bytes, bytes]:
        self.started.set()
        if self.block:
            await asyncio.Future()
        self.returncode = self.exit_code
        return b"", b""

    async def wait(self) -> int:
        self.waited = True
        if self.returncode is None:
            self.returncode = -signal.SIGTERM
        return self.returncode


def test_probe_tooling_uses_only_the_pinned_offline_npx_package(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    process = _Process(block=False)

    async def create_subprocess_exec(*args: str, **kwargs: object) -> _Process:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)

    result = asyncio.run(mcp_conformance._probe_tooling(Path("/repo")))

    args = captured["args"]
    assert isinstance(args, tuple)
    assert args[:3] == ("npx", "--offline", "--no")
    assert _CONFORMANCE in args
    assert args[-1] == "--help"
    assert result == 0


def _write_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def _captured_env(captured: dict[str, object]) -> dict[str, str]:
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    env = kwargs["env"]
    assert isinstance(env, dict)
    values: dict[str, str] = {}
    for key, value in env.items():
        assert isinstance(key, str)
        assert isinstance(value, str)
        values[key] = value
    return values


def _fake_fnm_bin(home: Path) -> Path:
    binary_dir = home / ".local" / "share" / "fnm" / "node-versions" / "v24.22.0" / "installation" / "bin"
    _write_executable(binary_dir / "node")
    _write_executable(binary_dir / "npx")
    return binary_dir


def test_probe_tooling_prepends_only_the_known_fnm_bin_when_npx_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "fake-home"
    fallback_bin = _fake_fnm_bin(home)
    initial_path = str(tmp_path / "empty-path")
    Path(initial_path).mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", initial_path)
    captured: dict[str, object] = {}
    process = _Process(block=False)

    async def create_subprocess_exec(*args: str, **kwargs: object) -> _Process:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)

    result = asyncio.run(mcp_conformance._probe_tooling(tmp_path))

    env = _captured_env(captured)
    assert env["PATH"] == f"{fallback_bin}{os.pathsep}{initial_path}"
    assert env["HOME"] == str(home)
    assert result == 0


def test_probe_tooling_preserves_preferred_npx_path_and_failure_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "fake-home"
    _ = _fake_fnm_bin(home)
    preferred_bin = tmp_path / "preferred-bin"
    _write_executable(preferred_bin / "npx")
    initial_path = f"{preferred_bin}{os.pathsep}{tmp_path / 'system-bin'}"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", initial_path)
    captured: dict[str, object] = {}
    process = _Process(block=False, exit_code=44)

    async def create_subprocess_exec(*args: str, **kwargs: object) -> _Process:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)

    result = asyncio.run(mcp_conformance._probe_tooling(tmp_path))

    assert _captured_env(captured)["PATH"] == initial_path
    assert result == 44


def test_probe_tooling_fails_missing_npx_without_install_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "fake-home"
    initial_path = str(tmp_path / "empty-path")
    Path(initial_path).mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", initial_path)
    calls: list[tuple[str, ...]] = []

    async def missing_executable(*args: str, **_kwargs: object) -> _Process:
        calls.append(args)
        raise FileNotFoundError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", missing_executable)

    result = asyncio.run(mcp_conformance._probe_tooling(tmp_path))

    assert result != 0
    assert calls == [("npx", "--offline", "--no", _CONFORMANCE, "--help")]


@pytest.mark.parametrize("termination", ["timeout", "cancel"])
def test_run_suite_terminates_and_reaps_its_process_group_on_timeout_or_cancel(
    monkeypatch: pytest.MonkeyPatch,
    termination: str,
) -> None:
    process = _Process(block=True)
    captured: dict[str, object] = {}
    signals: list[tuple[int, int]] = []

    async def create_subprocess_exec(*args: str, **kwargs: object) -> _Process:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return process

    def killpg(pid: int, signum: int) -> None:
        signals.append((pid, signum))
        process.returncode = -signum

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    monkeypatch.setattr(mcp_conformance.os, "killpg", killpg)

    async def exercise() -> int:
        task = asyncio.create_task(
            mcp_conformance._run_suite(Path("/repo"), "http://127.0.0.1:8765/mcp", 0.01)
        )
        await process.started.wait()
        if termination == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return -1
        return await task

    result = asyncio.run(exercise())

    args = captured["args"]
    assert isinstance(args, tuple)
    assert args[:3] == ("npx", "--offline", "--no")
    assert _CONFORMANCE in args
    assert "http://127.0.0.1:8765/mcp" in args
    assert _REVISION in args
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["start_new_session"] is True
    assert signals == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]
    assert process.waited is True
    if termination == "cancel":
        assert result == -1
    else:
        assert result == 124
