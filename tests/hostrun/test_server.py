"""hostrun rejects anything that is not a named action."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

ROOT = Path(__file__).resolve().parents[2]
# server.py imports `actions` as a top-level module, so its directory must be importable.
sys.path.insert(0, str(ROOT / "tools" / "hostrun"))

from tools.hostrun import actions, server  # noqa: E402

CLIENT = ROOT / "tools" / "hostrun" / "hostrun"
LIBPOD_LINE = (
    "0::/user.slice/user-1000.slice/user@1000.service/user.slice/"
    "libpod-4e4a354dcbd42e8423ab1cd43fb654d8396af1325a8a0833e2c66e4f8706c959.scope/container\n"
)
Running = tuple[server.Policy, dict[str, str | None]]


def _policy(tmp: Path) -> server.Policy:
    root = tmp / "Projects"
    repo = root / "app"
    repo.mkdir(parents=True)
    example = server.load_policy(ROOT / "tools" / "hostrun" / "policy.example.toml")
    containers = {
        name: server.ContainerPolicy(actions=spec.actions, worktree=str(repo))
        for name, spec in example.containers.items()
    }
    return server.Policy(
        projects_root=str(root),
        repo=str(repo),
        socket_path=str(tmp / "hostrun.sock"),
        audit_path=str(tmp / "audit.jsonl"),
        state_dir=str(tmp / "state"),
        output_limit_bytes=example.output_limit_bytes,
        default_timeout_s=example.default_timeout_s,
        timeouts=example.timeouts,
        containers=containers,
    )


def _start(
    policy: server.Policy, resolve: Callable[[int], str | None]
) -> tuple[server.ThreadingUnixHTTPServer, threading.Thread]:
    app = server.Application(policy, resolve_peer=resolve)
    httpd = server.ThreadingUnixHTTPServer(policy.socket_path, app.handler_class())
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    for _ in range(50):
        if Path(policy.socket_path).exists():
            break
        time.sleep(0.02)
    return httpd, thread


def _post(sock_path: str, payload: dict[str, object]) -> tuple[int, bytes]:
    body = json.dumps(payload).encode()
    request = (
        "POST /run HTTP/1.0\r\n"
        "Host: localhost\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode() + body
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(sock_path)
    sock.sendall(request)
    chunks: list[bytes] = []
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
    sock.close()
    raw = b"".join(chunks)
    head, _, payload_bytes = raw.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0]
    status = int(status_line.split()[1])
    return status, payload_bytes


@pytest.fixture
def running(tmp_path: Path) -> Iterator[Running]:
    holder: dict[str, str | None] = {"name": "agent-cursor"}
    policy = _policy(tmp_path)

    def resolve(_pid: int) -> str | None:
        return holder["name"]

    httpd, thread = _start(policy, resolve)
    yield policy, holder
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


def test_gate_ids_match_the_plan(tmp_path: Path) -> None:
    repo = str(tmp_path)
    accepted = ("A1.1", "A1.10", "A1.1b", "A0.0r", "A2.1br", "A1.6r", "H1", "R0b", "I0", "B5a")
    for task_id in accepted:
        plan = actions.build_plan("gate", [task_id], tmp_path, repo, 30)
        assert plan.steps[-1].argv[-1] == task_id
    with pytest.raises(actions.ActionError, match="task id"):
        _ = actions.build_plan("gate", ["A1.1bb"], tmp_path, repo, 30)


def test_gate_audit_core_builds_the_audit_argv(tmp_path: Path) -> None:
    repo = str(tmp_path)
    plan = actions.build_plan("gate", ["--audit", "core"], tmp_path, repo, 30)
    argv = plan.steps[-1].argv
    assert argv[0] == "podman"
    assert f"{repo}/tools/gate/phase_gate.py" in argv
    assert argv[-2:] == ("--audit", "core")
    assert "PYTHON_GIL=0" in argv


def test_gate_audit_ui_is_rejected(tmp_path: Path) -> None:
    repo = str(tmp_path)
    with pytest.raises(actions.ActionError, match="task id or --audit core") as raised:
        _ = actions.build_plan("gate", ["--audit", "ui"], tmp_path, repo, 30)
    assert raised.value.status == 400


def test_gate_audit_ui_is_400_over_http(running: Running) -> None:
    policy, _holder = running
    status, body = _post(policy.socket_path, {"action": "gate", "args": ["--audit", "ui"]})
    assert status == 400
    assert b"--audit core" in body


def test_policy_rejects_a_container_without_worktree(tmp_path: Path) -> None:
    source = (ROOT / "tools" / "hostrun" / "policy.example.toml").read_text(encoding="utf-8")
    head, tail = source.split("[containers.agent-codex]", 1)
    broken = (
        head
        + "[containers.agent-codex]"
        + tail.replace('worktree = "@REPO@"\n', "", 1)
    )
    assert broken != source
    path = tmp_path / "policy.toml"
    _ = path.write_text(broken, encoding="utf-8")
    with pytest.raises(server.PolicyError, match="worktree") as raised:
        _ = server.load_policy(path)
    assert "worktree" in str(raised.value)


def test_default_paths_stay_inside_the_suite(tmp_path: Path) -> None:
    repo = tmp_path / "core"
    repo.mkdir()
    core = actions.build_plan("test", ["core"], repo, str(repo), 30).steps[-1]
    assert core.argv[0] == "podman"
    assert core.argv[-5:] == ("tests/core", "tests/contracts", "tests/gate", "tests/hostrun", "tests/smoke")
    assert "tests/ui" not in core.argv
    ui = actions.build_plan("test", ["ui"], repo, str(repo), 30).steps[-1]
    assert ui.argv[-2:] == ("tests/smoke", "tests/ui")
    fake = actions.build_plan(
        "fake-core",
        ["start", "demo"],
        repo,
        str(repo),
        30,
        runtime_dir=str(tmp_path / "rt"),
        side="agent-cursor",
    )
    assert fake.mode == "exec"
    assert fake.steps[-1].argv[-4:] == (str(tmp_path / "rt"), "agent-cursor", "start", "demo")
    assert fake.side == "agent-cursor"
    gate = actions.build_plan("gate", ["R0c"], repo, str(repo), 30)
    assert f"{repo}/tools/gate/phase_gate.py" in gate.steps[-1].argv
    assert gate.steps[-1].argv[0] == "podman"


def test_example_policy_loads() -> None:
    policy = server.load_policy(ROOT / "tools" / "hostrun" / "policy.example.toml")
    assert "check" in policy.containers["agent-cursor"].actions
    assert policy.containers["agent-cursor"].worktree== str(ROOT)
    assert "gate" in policy.containers["agent-claude"].actions
    assert "gate" not in policy.containers["agent-codex"].actions
    assert "core-run" not in policy.containers["agent-codex"].actions


def test_example_policy_names_no_machine() -> None:
    source = (ROOT / "tools" / "hostrun" / "policy.example.toml").read_text(encoding="utf-8")
    assert "/home/" not in source
    policy = server.load_policy(ROOT / "tools" / "hostrun" / "policy.example.toml")
    assert policy.repo == str(ROOT)
    assert policy.projects_root == str(ROOT.parent)
    assert policy.socket_path == str(Path.home() / ".containers" / "hostrun" / "hostrun.sock")


def test_example_policy_binds_every_container_to_the_main_checkout() -> None:
    policy = server.load_policy(ROOT / "tools" / "hostrun" / "policy.example.toml")
    assert set(policy.containers) == {"agent-grok", "agent-cursor", "agent-claude", "agent-codex"}
    for name, container in policy.containers.items():
        assert container.worktree.startswith("/"), name
    for name in ("agent-grok", "agent-cursor", "agent-codex", "agent-claude"):
        assert policy.containers[name].worktree == str(ROOT), name
    assert "gate" in policy.containers["agent-grok"].actions


def test_cgroup_extracts_libpod_id() -> None:
    assert (
        server.container_id_from_cgroup(LIBPOD_LINE)
        == "4e4a354dcbd42e8423ab1cd43fb654d8396af1325a8a0833e2c66e4f8706c959"
    )
    assert server.container_id_from_cgroup("0::/user.slice/user-1000.slice/session-1.scope\n") is None


def test_refuses_root() -> None:
    with pytest.raises(SystemExit):
        server.ensure_not_root(lambda: 0)


def test_unknown_action_rejected(running: Running) -> None:
    policy, _holder = running
    status, body = _post(policy.socket_path, {"action": "rm", "args": ["-rf", "/"]})
    assert status == 400
    assert b"unknown action" in body


def test_cwd_dotdot_rejected(running: Running) -> None:
    policy, _holder = running
    status, body = _post(
        policy.socket_path,
        {"action": "check", "args": [], "cwd": f"{policy.projects_root}/sub/../../etc"},
    )
    assert status == 400
    assert b"cwd contains .." in body


def test_cwd_symlink_escape_rejected(running: Running, tmp_path: Path) -> None:
    policy, _holder = running
    outside = tmp_path / "outside"
    outside.mkdir()
    link = Path(policy.projects_root) / "escape"
    link.symlink_to(outside)
    status, body = _post(policy.socket_path, {"action": "check", "args": [], "cwd": str(link)})
    assert status == 400
    assert b"cwd escapes projects" in body


def test_shell_metacharacters_are_not_executed(running: Running, tmp_path: Path) -> None:
    policy, _holder = running
    marker = tmp_path / "pwned"
    status, body = _post(
        policy.socket_path,
        {"action": "git", "args": [f"status; touch {marker}"]},
    )
    assert status == 400
    assert b"git arguments are not allowed" in body
    assert not marker.exists()
    source = (ROOT / "tools" / "hostrun" / "server.py").read_text(encoding="utf-8")
    actions_source = (ROOT / "tools" / "hostrun" / "actions.py").read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert "shell=True" not in actions_source


def test_runner_does_not_use_shell(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    chunks: list[bytes] = []
    code = server.execute_argv(
        ("/bin/echo", f"; touch {marker}"),
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin"},
        deadline=time.monotonic() + 5,
        output_limit=10000,
        written=[0],
        write=chunks.append,
    )
    assert code == 0
    assert not marker.exists()
    assert b"; touch" in b"".join(chunks)


def test_timeout(tmp_path: Path) -> None:
    started = time.monotonic()
    code = server.execute_argv(
        ("/bin/sleep", "30"),
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin"},
        deadline=time.monotonic() + 0.4,
        output_limit=1000,
        written=[0],
        write=lambda _data: None,
    )
    assert code == 124
    assert time.monotonic() - started < 3


def test_output_limit(tmp_path: Path) -> None:
    chunks: list[bytes] = []
    written = [0]
    code = server.execute_argv(
        (sys.executable, "-c", "import sys; sys.stdout.write('x' * 200000)"),
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin"},
        deadline=time.monotonic() + 5,
        output_limit=1024,
        written=written,
        write=chunks.append,
    )
    assert code == 125
    payload = b"".join(chunks)
    assert written[0] <= 1024
    assert len(payload) < 8000
    assert b"output limit exceeded" in payload


def test_run_plan_discards_startup_output_on_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = _policy(tmp_path)
    repo = Path(policy.repo)
    manifest = repo / "crates" / "core" / "Cargo.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("[workspace]\nmembers = []\n", encoding="utf-8")
    plan = actions.build_plan("devtools", ["rust-metadata", "crates/core"], repo, policy.repo, 30)
    assert plan.steps[0].quiet_success is True
    calls: list[tuple[str, ...]] = []
    deadlines: list[float] = []
    output_limits: list[int] = []

    def fake_execute(
        argv: tuple[str, ...],
        *,
        cwd: str,
        env: dict[str, str],
        deadline: float,
        output_limit: int,
        written: list[int],
        write: Callable[[bytes], None],
    ) -> int:
        del cwd, env, written
        calls.append(argv)
        deadlines.append(deadline)
        output_limits.append(output_limit)
        if argv == plan.steps[0].argv:
            write(b"devtools\n")
            return 0
        write(b'{"packages":[]}\n')
        return 0

    monkeypatch.setattr(server, "execute_argv", fake_execute)
    output: list[bytes] = []

    code = server.Application(policy).run_plan(
        plan,
        cwd=policy.repo,
        write=output.append,
        container="agent-codex",
    )

    assert code == 0
    assert calls == [step.argv for step in plan.steps]
    assert len(deadlines) == 2 and deadlines[0] == deadlines[1]
    assert output_limits == [policy.output_limit_bytes, policy.output_limit_bytes]
    assert b"".join(output) == b'{"packages":[]}\n'


def test_run_plan_preserves_startup_diagnostic_and_stops_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = _policy(tmp_path)
    repo = Path(policy.repo)
    manifest = repo / "crates" / "core" / "Cargo.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("[workspace]\nmembers = []\n", encoding="utf-8")
    plan = actions.build_plan("devtools", ["rust-metadata", "crates/core"], repo, policy.repo, 30)
    assert plan.steps[0].quiet_success is True
    calls: list[tuple[str, ...]] = []
    diagnostic = b"podman start devtools failed\n"

    def fake_execute(
        argv: tuple[str, ...],
        *,
        cwd: str,
        env: dict[str, str],
        deadline: float,
        output_limit: int,
        written: list[int],
        write: Callable[[bytes], None],
    ) -> int:
        del cwd, env, deadline, output_limit, written
        calls.append(argv)
        write(diagnostic)
        return 125

    monkeypatch.setattr(server, "execute_argv", fake_execute)
    output: list[bytes] = []

    code = server.Application(policy).run_plan(
        plan,
        cwd=policy.repo,
        write=output.append,
        container="agent-codex",
    )

    assert code == 125
    assert calls == [plan.steps[0].argv]
    assert b"".join(output) == diagnostic


def test_run_plan_streams_output_from_ordinary_steps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = _policy(tmp_path)
    plan = actions.Plan(
        mode="exec",
        steps=(actions.Step(argv=("ordinary-action",)),),
        timeout_s=30,
    )
    diagnostic = b"ordinary action diagnostic\n"

    def fake_execute(
        _argv: tuple[str, ...],
        *,
        cwd: str,
        env: dict[str, str],
        deadline: float,
        output_limit: int,
        written: list[int],
        write: Callable[[bytes], None],
    ) -> int:
        del cwd, env, deadline, output_limit, written
        write(diagnostic)
        return 17

    monkeypatch.setattr(server, "execute_argv", fake_execute)
    output: list[bytes] = []

    code = server.Application(policy).run_plan(
        plan,
        cwd=policy.repo,
        write=output.append,
        container="agent-codex",
    )

    assert code == 17
    assert b"".join(output) == diagnostic


def test_unknown_container_returns_403(running: Running) -> None:
    policy, holder = running
    holder["name"] = None
    status, body = _post(policy.socket_path, {"action": "check", "args": []})
    assert status == 403
    assert b"unknown container" in body


def test_host_peer_without_container_is_403(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    httpd, thread = _start(policy, server.resolve_container_name)
    try:
        status, body = _post(policy.socket_path, {"action": "check", "args": []})
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
    assert status == 403
    assert b"unknown container" in body


def test_unidentified_peer_and_unlisted_container_have_different_403s(running: Running) -> None:
    policy, holder = running
    holder["name"] = None
    status, body = _post(policy.socket_path, {"action": "check", "args": []})
    assert status == 403
    assert b"unknown container: peer is not an identified podman container" in body
    holder["name"] = "agent-stranger"
    status, body = _post(policy.socket_path, {"action": "check", "args": []})
    assert status == 403
    assert b"unknown container: agent-stranger is not in policy" in body
    assert b"peer is not an identified" not in body


def _install_check(repo: str, body: str) -> None:
    script = Path(repo) / "tools" / "check.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    _ = script.write_text(body, encoding="utf-8")
    script.chmod(0o755)


def _capture_exec(
    monkeypatch: pytest.MonkeyPatch,
    *,
    code: int = 0,
    output: bytes = b"",
) -> list[tuple[str, ...]]:
    """Stand in for process start so a test never runs checkout code or podman."""
    seen: list[tuple[str, ...]] = []

    def fake_execute(
        argv: tuple[str, ...],
        *,
        cwd: str,
        env: dict[str, str],
        deadline: float,
        output_limit: int,
        written: list[int],
        write: Callable[[bytes], None],
    ) -> int:
        del cwd, env, deadline, output_limit, written
        seen.append(argv)
        if argv[:3] == ("podman", "start", "devtools"):
            return 0
        if output:
            write(output)
        return code

    monkeypatch.setattr(server, "execute_argv", fake_execute)
    return seen


def test_check_names_the_tools(running: Running, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    policy, _holder = running
    marker = tmp_path / "host-ran"
    _install_check(policy.repo, f"#!/bin/sh\ntouch '{marker}'\nprintf '%s\\n' ruff\n")
    seen = _capture_exec(monkeypatch, output=b"ruff\nbasedpyright\n")
    status, body = _post(policy.socket_path, {"action": "check", "args": []})
    assert status == 200
    assert b"ruff\n" in body
    assert b"basedpyright\n" in body
    assert b"__EXIT__ 0\n" in body
    assert not marker.exists()
    assert seen[0] == ("podman", "start", "devtools")
    assert seen[1][0] == "podman"
    assert seen[1][-2:] == ("sh", f"{policy.repo}/tools/check.sh")
    assert "HOSTRUN_CONTAINER=agent-cursor" in seen[1]
    audit = Path(policy.audit_path).read_text(encoding="utf-8")
    assert '"container":"agent-cursor"' in audit
    assert '"action":"check"' in audit


def test_check_passes_the_container_name_into_devtools(running: Running, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    policy, _holder = running
    dump = tmp_path / "env-dump"
    _install_check(policy.repo, f"#!/bin/sh\nprintf '%s' \"$HOSTRUN_CONTAINER\" > '{dump}'\n")
    seen = _capture_exec(monkeypatch)
    status, _body = _post(policy.socket_path, {"action": "check", "args": []})
    assert status == 200
    assert not dump.exists()
    assert "HOSTRUN_CONTAINER=agent-cursor" in seen[-1]


def test_check_fails_when_a_tool_fails(running: Running, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    policy, _holder = running
    marker = tmp_path / "host-ran"
    _install_check(policy.repo, f"#!/bin/sh\ntouch '{marker}'\nprintf '%s\\n' ruff\nexit 1\n")
    _ = _capture_exec(monkeypatch, code=1, output=b"ruff\n")
    status, body = _post(policy.socket_path, {"action": "check", "args": []})
    assert status == 200
    assert b"ruff\n" in body
    assert b"__EXIT__ 1\n" in body
    assert not marker.exists()


def test_codex_cannot_gate(running: Running) -> None:
    policy, holder = running
    holder["name"] = "agent-codex"
    status, body = _post(policy.socket_path, {"action": "gate", "args": ["A0.2"]})
    assert status == 403
    assert b"action not permitted" in body


def test_deps_sync_interpreters(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    repo.mkdir()
    core = actions.build_plan("deps-sync", ["core"], repo, str(repo), 30)
    ui = actions.build_plan("deps-sync", ["ui"], repo, str(repo), 30)
    assert _devtools_command(core.steps[-1].argv, str(repo)) == (
        actions._UV,
        "venv",
        "--no-project",
        "--no-python-downloads",
        "--python",
        "3.15t",
        "--allow-existing",
        str(repo / ".venv-core"),
    )
    assert _devtools_command(ui.steps[-1].argv, str(repo))[5] == "3.15+gil"
    assert "3.15t" not in ui.steps[-1].argv
    _ = (repo / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n", encoding="utf-8")
    synced = actions.build_plan("deps-sync", ["core"], repo, str(repo), 30)
    assert _devtools_command(synced.steps[-1].argv, str(repo)) == (
        actions._UV,
        "sync",
        "--locked",
        "--no-python-downloads",
        "--python",
        "3.15t",
    )
    assert f"UV_PROJECT_ENVIRONMENT={repo}/.venv-core" in synced.steps[-1].argv
    synced_ui = actions.build_plan("deps-sync", ["ui"], repo, str(repo), 30)
    assert _devtools_command(synced_ui.steps[-1].argv, str(repo)) == (
        actions._UV,
        "sync",
        "--locked",
        "--group",
        "ui",
        "--no-python-downloads",
        "--python",
        "3.15+gil",
    )
    assert f"UV_PROJECT_ENVIRONMENT={repo}/.venv-ui" in synced_ui.steps[-1].argv
    core_test = actions.build_plan("test", ["core", "-k", "smoke"], repo, str(repo), 30).steps[-1]
    assert "PYTHON_GIL=0" in core_test.argv
    ui_test = actions.build_plan("test", ["ui"], repo, str(repo), 30).steps[-1]
    assert ui_test.argv[0] == "podman"
    assert f"{repo}/.venv-ui/bin/python" in ui_test.argv
    assert ui_test.argv[-1] == "tests/ui"
    assert "tests/core" not in ui_test.argv
    assert "PYTHON_GIL=0" not in ui_test.argv
    assert ui_test.env == ()
    hostrun = actions.build_plan("test", ["hostrun"], repo, str(repo), 30).steps[-1]
    assert hostrun.argv[-1] == "tests/hostrun"
    with pytest.raises(actions.ActionError, match="core or ui"):
        _ = actions.build_plan("deps-sync", ["both"], repo, str(repo), 30)


def test_client_check_and_rejects_shell_args(
    running: Running,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy, _holder = running
    marker = tmp_path / "host-ran"
    _install_check(policy.repo, f"#!/bin/sh\ntouch '{marker}'\nprintf '%s\\n' ruff basedpyright\n")
    _ = _capture_exec(monkeypatch, output=b"ruff\nbasedpyright\n")
    env = {**os.environ, "HOSTRUN_SOCK": policy.socket_path}
    ok = subprocess.run(
        ["/bin/sh", str(CLIENT), "check"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert ok.returncode == 0
    assert "ruff" in ok.stdout
    assert "basedpyright" in ok.stdout
    blocked = subprocess.run(
        ["/bin/sh", str(CLIENT), "git", f"status; touch {marker}"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert blocked.returncode != 0
    assert not marker.exists()


MCP_URL = "http://127.0.0.1:8765/mcp"
CONFORMANCE_ARGV_TAIL = (
    "npx",
    "--yes",
    "@modelcontextprotocol/conformance@0.2.0-alpha.12",
    "server",
    "--url",
    MCP_URL,
    "--requirements",
    "2026-07-28",
)


def test_conformance_server_argv_is_exact(tmp_path: Path) -> None:
    repo = str(tmp_path)
    plan = actions.build_plan("conformance", ["server", MCP_URL], tmp_path, repo, 30)
    assert plan.mode == "exec"
    assert plan.steps[0].argv == ("podman", "start", "devtools")
    assert plan.steps[1].argv == ("podman", "exec", "--workdir", repo, "devtools", *CONFORMANCE_ARGV_TAIL)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8765/mcp",
        "http://0.0.0.0:8765/mcp",
        "http://127.0.0.2:8765/mcp",
        "https://127.0.0.1:8765/mcp",
        "http://127.0.0.1:8765/other",
        "http://127.0.0.1:8765/mcp?x=1",
        "http://127.0.0.1:1023/mcp",
        "http://127.0.0.1:80/mcp",
        "http://127.0.0.1:65536/mcp",
        "http://127.0.0.1:99999/mcp",
        "http://127.0.0.1:8765/mcp;rm -rf /",
        "http://127.0.0.1:8765/mcp$(id)",
        "http://127.0.0.1:8765/mcp && id",
    ],
)
def test_conformance_rejects_other_urls(tmp_path: Path, url: str) -> None:
    with pytest.raises(actions.ActionError) as raised:
        _ = actions.build_plan("conformance", ["server", url], tmp_path, str(tmp_path), 30)
    assert raised.value.status == 400
    assert "conformance expects" in raised.value.message


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["server"],
        ["server", MCP_URL, "extra"],
        ["client", MCP_URL],
    ],
)
def test_conformance_rejects_wrong_argument_shape(tmp_path: Path, args: list[str]) -> None:
    with pytest.raises(actions.ActionError) as raised:
        _ = actions.build_plan("conformance", args, tmp_path, str(tmp_path), 30)
    assert raised.value.status == 400


DEVTOOLS_CASES = [
    ("python", "src/aconsole", ("python3", "tools/codegraph/extract_python.py", "src/aconsole")),
    ("ts", "src", ("npx", "--yes", "dependency-cruiser@18.7.0", "--output-type", "json", "src")),
    ("rust-metadata", "crates/core", None),
    ("csharp", "src/Cs", ("dotnet", "run", "--project", "tools/csgraph", "--", "src/Cs")),
]


@pytest.mark.parametrize(("extractor", "path", "tail"), DEVTOOLS_CASES)
def test_devtools_extractor_argv(
    tmp_path: Path,
    extractor: str,
    path: str,
    tail: tuple[str, ...] | None,
) -> None:
    repo = str(tmp_path)
    manifest: Path | None = None
    if extractor == "rust-metadata":
        manifest = tmp_path / path / "Cargo.toml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("[workspace]\nmembers = []\n", encoding="utf-8")
    plan = actions.build_plan("devtools", [extractor, path], tmp_path, repo, 30)
    assert plan.steps[0].argv == ("podman", "start", "devtools")
    argv = plan.steps[1].argv
    if extractor == "rust-metadata":
        assert manifest is not None
        _assert_rust_metadata_wrapper(argv, repo, manifest)
    else:
        assert tail is not None
        assert argv == ("podman", "exec", "--workdir", repo, "devtools", *tail)


def _assert_rust_metadata_wrapper(argv: tuple[str, ...], repo: str, manifest: Path) -> None:
    assert argv[:5] == ("podman", "exec", "--workdir", repo, "devtools")
    assert argv[5:7] == ("/bin/sh", "-c")
    script = argv[7]
    assert "--offline" in script
    assert "$1" in script
    assert str(manifest.resolve()) not in script
    assert argv[-2:] == ("aconsole-rust", str(manifest.resolve()))


def test_devtools_rust_metadata_appends_cargo_toml_after_a_trailing_slash(tmp_path: Path) -> None:
    repo = str(tmp_path)
    manifest = tmp_path / "crates" / "core" / "Cargo.toml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("[workspace]\nmembers = []\n", encoding="utf-8")
    plan = actions.build_plan("devtools", ["rust-metadata", "crates/core/"], tmp_path, repo, 30)
    _assert_rust_metadata_wrapper(plan.steps[-1].argv, repo, manifest)


@pytest.mark.parametrize(
    "args",
    [
        ["rust-modules", "src"],
        ["Python", "src"],
        ["python", "/etc"],
        ["python", "src/../etc"],
        ["python", ".."],
        ["python", "-rf"],
        ["python", "--output"],
        ["python", "src;rm -rf /"],
        ["python", "src$(id)"],
        ["python", "src|cat"],
        ["python", "src`id`"],
        ["python", "src with space"],
        ["python", ""],
        [],
        ["python"],
        ["python", "src", "extra"],
    ],
)
def test_devtools_rejects_bad_extractor_path_or_count(tmp_path: Path, args: list[str]) -> None:
    with pytest.raises(actions.ActionError) as raised:
        _ = actions.build_plan("devtools", args, tmp_path, str(tmp_path), 30)
    assert raised.value.status == 400


def test_codex_cannot_run_conformance_or_devtools(running: Running) -> None:
    policy, holder = running
    holder["name"] = "agent-codex"
    for action, args in (("conformance", ["server", MCP_URL]), ("devtools", ["python", "src"])):
        status, body = _post(policy.socket_path, {"action": action, "args": args})
        assert status == 403
        assert b"action not permitted" in body


def test_conformance_and_devtools_reject_bad_input_over_http(running: Running) -> None:
    policy, _holder = running
    status, body = _post(policy.socket_path, {"action": "conformance", "args": ["server", "http://localhost:8765/mcp"]})
    assert status == 400
    assert b"conformance expects" in body
    status, body = _post(policy.socket_path, {"action": "devtools", "args": ["rust-modules", "src"]})
    assert status == 400
    assert b"devtools expects one of" in body


def test_fake_start_step_carries_hostrun_container(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    env_file = tmp_path / "container-env"
    sock = tmp_path / "demo.sock"
    # The stub writes HOSTRUN_CONTAINER, creates the socket the server waits for, then sleeps briefly.
    stub = (
        'printf "%s" "$HOSTRUN_CONTAINER" > "$1"; : > "$2"; sleep 3',
        "sh",
        str(env_file),
        str(sock),
    )
    plan = actions.Plan(
        mode="fake-start",
        steps=(actions.Step(argv=("/bin/sh", "-c", *stub)),),
        timeout_s=30,
        scenario="demo",
        socket_path=str(sock),
        side="agent-cursor",
    )
    written: list[bytes] = []
    code = server.Application(policy).run_plan(plan, cwd=str(tmp_path), write=written.append, container="agent-grok")
    pid_path = Path(policy.state_dir) / "fake-core" / "agent-cursor-demo.pid"
    if pid_path.exists():
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(pid_path.read_text(encoding="utf-8").strip()), signal.SIGTERM)
    assert code == 0, b"".join(written)
    assert env_file.read_text(encoding="utf-8") == "agent-grok"


def _devtools_command(argv: tuple[str, ...], repo: str) -> tuple[str, ...]:
    assert argv[:4] == ("podman", "exec", "--workdir", repo)
    rest = argv[4:]
    while rest[:1] == ("-e",):
        rest = rest[2:]
    assert rest[0] == "devtools"
    return rest[1:]


def test_check_test_and_gate_keep_checkout_code_inside_devtools(tmp_path: Path) -> None:
    """The host process is podman. A rewritten check script is only an argument, so this test never starts it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    script = repo / "tools" / "check.sh"
    script.parent.mkdir()
    marker = tmp_path / "host-ran"
    _ = script.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    script.chmod(0o755)
    cases = (
        ("check", []),
        ("test", ["core", "-k", "smoke"]),
        ("test", ["gui"]),
        ("gate", ["A1"]),
        ("gate", ["--audit", "core"]),
    )
    for action, args in cases:
        plan = actions.build_plan(action, list(args), repo, str(repo), 30, side="agent-cursor")
        assert [step.argv[0] for step in plan.steps] == ["podman", "podman"]
        assert plan.steps[0].argv == ("podman", "start", "devtools")
        command = _devtools_command(plan.steps[1].argv, str(repo))
        assert "HOSTRUN_CONTAINER=agent-cursor" in plan.steps[1].argv
        assert command[0] not in {"podman", "sh"} or action == "check"
        if action == "check":
            assert command == ("sh", str(script))
        if action == "gate":
            assert command[1] == f"{repo}/tools/gate/phase_gate.py"
            assert "PYTHON_GIL=0" in plan.steps[1].argv
        if action == "test" and args[0] == "core":
            assert command[0] == f"{repo}/.venv-core/bin/python"
            assert "PYTHON_GIL=0" in plan.steps[1].argv
    refused = ("git", ["diff", "--textconv"])
    with pytest.raises(actions.ActionError, match="not allowed"):
        _ = actions.build_plan(refused[0], list(refused[1]), repo, str(repo), 30)
    assert not marker.exists()


def test_git_diff_does_not_run_repo_diff_drivers(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    marker = tmp_path / "driver-ran"
    driver = tmp_path / "driver.sh"
    _ = driver.write_text(
        f"#!/bin/sh\nprintf ran >> '{marker}'\nprintf 'hijacked\\n'\n",
        encoding="utf-8",
    )
    driver.chmod(0o755)
    _git(repo, "init", "-q")
    _ = (repo / "note.txt").write_text("before\n", encoding="utf-8")
    _ = (repo / ".gitattributes").write_text("*.txt diff=evil\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "config", "diff.external", str(driver))
    _git(repo, "config", "diff.evil.textconv", str(driver))
    _ = (repo / "note.txt").write_text("after\n", encoding="utf-8")
    plan = actions.build_plan("git", ["diff"], repo, str(repo), 30)
    argv = plan.steps[0].argv
    assert argv == ("git", "-C", str(repo), "diff", "--no-ext-diff", "--no-textconv")
    chunks: list[bytes] = []
    code = server.execute_argv(
        argv,
        cwd=str(repo),
        env=server.base_env(),
        deadline=time.monotonic() + 10,
        output_limit=100_000,
        written=[0],
        write=chunks.append,
    )
    text = b"".join(chunks)
    assert code == 0, text
    assert b"after" in text
    assert b"hijacked" not in text
    assert not marker.exists()


def _git(repo: Path, *args: str) -> None:
    completed = subprocess.run(("git", "-C", str(repo), *args), check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(("action", "args"), [
    ("deps-sync", ["core"]), ("deps-sync", ["ui"]),
    ("core-run", []), ("ui-run", []), ("ui-run", ["--spike"]),
    ("ui-run", ["--fake", "demo"]), ("ui-snapshot", ["--spike"]),
    ("ui-snapshot", ["--scenario", "demo", "--view", "plan", "--theme", "dark",
                     "--size", "1280x800", "--out", "docs/ui/demo.png"]),
    ("fake-core", ["start", "demo"]), ("fake-core", ["stop", "demo"]),
])
def test_remaining_checkout_actions_only_execute_podman(tmp_path: Path, action: str, args: list[str]) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='untrusted'\nversion='0'\n")
    plan = actions.build_plan(action, args, repo, str(repo), 30, runtime_dir=str(tmp_path / "runtime"),
                              side="agent-cursor")
    assert plan.mode == "exec"
    assert plan.steps[0].argv == ("podman", "start", "devtools")
    for step in plan.steps[1:]:
        assert step.argv[0] == "podman"
        assert step.env == ()
        command = _devtools_command(step.argv, str(repo))
        assert command
        assert "HOSTRUN_CONTAINER=agent-cursor" in step.argv
    if action == "fake-core":
        assert command[1:] == (f"{repo}/tools/hostrun/server.py", "--container-fake", str(repo),
                               str(tmp_path / "runtime"), "agent-cursor", *args)
    if action.startswith("ui-"):
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XAUTHORITY"):
            assert key in plan.steps[-1].argv


@pytest.mark.parametrize(("action", "args"), [
    ("core-run", ["--eval"]), ("deps-sync", ["core; touch marker"]),
    ("ui-run", ["--fake", "../escape"]), ("ui-snapshot", ["--out", "/escape.png"]),
    ("fake-core", ["start", "../escape"]), ("fake-core", ["exec", "demo"]),
])
def test_remaining_checkout_actions_keep_argument_validation(tmp_path: Path, action: str, args: list[str]) -> None:
    with pytest.raises(actions.ActionError):
        actions.build_plan(action, args, tmp_path, str(tmp_path), 30)


@pytest.mark.parametrize("operation", ["start", "stop"])
def test_container_fake_entry_uses_container_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                     operation: str) -> None:
    policy = _policy(tmp_path)
    runtime = str(tmp_path / "runtime")
    monkeypatch.setattr(sys, "argv", ["server.py", "--container-fake", policy.repo,
                                     runtime, "agent-cursor", operation, "demo"])
    monkeypatch.setattr(server, "ensure_not_root", lambda: None)
    monkeypatch.setattr(server, "load_policy", lambda _path: policy)
    seen = []

    def run(app: server.Application, plan: actions.Plan, *, cwd: str,
            write: Callable[[bytes], None], container: str) -> int:
        del write
        seen.append((app.policy.state_dir, plan, cwd, container))
        return 7

    monkeypatch.setattr(server.Application, "run_plan", run)
    with pytest.raises(SystemExit) as caught:
        server.main()
    assert caught.value.code == 7
    state, plan, cwd, side = seen[0]
    assert state == runtime
    assert cwd == policy.repo
    assert side == "agent-cursor"
    assert plan.mode == ("fake-start" if operation == "start" else "fake-stop")
    assert plan.steps[0].argv == (f"{policy.repo}/.venv-core/bin/python", "-m", "aconsole.core",
                                 "--fake", "demo", "--socket", f"{runtime}/demo.sock")


@pytest.mark.parametrize(("action", "args"), [
    ("core-run", []), ("deps-sync", ["core"]), ("ui-run", ["--fake", "demo"]),
    ("ui-snapshot", ["--spike"]), ("fake-core", ["start", "demo"]),
    ("fake-core", ["stop", "demo"]),
])
def test_remaining_actions_dispatch_only_container_commands(running: Running, monkeypatch: pytest.MonkeyPatch,
                                                            action: str, args: list[str]) -> None:
    seen = _capture_exec(monkeypatch)
    status, body = _post(running[0].socket_path, {"action": action, "args": args})
    assert status == 200
    assert b"__EXIT__ 0" in body
    assert len(seen) >= 2
    assert all(argv[0] == "podman" for argv in seen)


def test_container_fake_lifecycle_starts_and_stops_a_real_process(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    repo = Path(policy.repo)
    python = repo / ".venv-core/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    package = repo / "aconsole"
    package.mkdir()
    (package / "__init__.py").touch()
    (package / "core.py").write_text(
        "import socket, sys, time\ns = socket.socket(socket.AF_UNIX)\n"
        "s.bind(sys.argv[-1])\ntime.sleep(60)\n",
    )
    with tempfile.TemporaryDirectory(prefix="hf-", dir="/tmp") as runtime:
        app = server.Application(policy)
        start = actions.container_fake_plan(["start", "demo"], str(repo), 30, runtime, "agent-cursor")
        output = []
        pid = None
        try:
            assert app.run_plan(start, cwd=str(repo), write=output.append, container="agent-cursor") == 0
            pid_file = Path(policy.state_dir) / "fake-core/agent-cursor-demo.pid"
            pid = int(pid_file.read_text())
            assert Path(start.socket_path).exists()
            assert app.run_plan(start, cwd=str(repo), write=output.append, container="agent-cursor") == 0
            assert b"already running" in b"".join(output)
            stop = actions.container_fake_plan(["stop", "demo"], str(repo), 30, runtime, "agent-cursor")
            assert app.run_plan(stop, cwd=str(repo), write=output.append) == 0
            assert not pid_file.exists()
            assert server._cmdline(pid) in (None, [])
        finally:
            if pid is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
                with contextlib.suppress(ChildProcessError):
                    os.waitpid(pid, 0)
