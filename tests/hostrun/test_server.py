"""hostrun rejects anything that is not a named action."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
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
        assert plan.steps[0].argv[-1] == task_id
    with pytest.raises(actions.ActionError, match="task id"):
        _ = actions.build_plan("gate", ["A1.1bb"], tmp_path, repo, 30)


def test_gate_audit_core_builds_the_audit_argv(tmp_path: Path) -> None:
    repo = str(tmp_path)
    plan = actions.build_plan("gate", ["--audit", "core"], tmp_path, repo, 30)
    argv = plan.steps[0].argv
    assert argv[1] == f"{repo}/tools/gate/phase_gate.py"
    assert argv[-2:] == ("--audit", "core")
    assert ("PYTHON_GIL", "0") in plan.steps[0].env


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
    broken = source.replace('worktree = "/home/sas/Projects/.aconsole-worktrees/sp_aconsole_prototype/front"\n', "", 1)
    assert broken != source
    path = tmp_path / "policy.toml"
    _ = path.write_text(broken, encoding="utf-8")
    with pytest.raises(server.PolicyError, match="worktree") as raised:
        _ = server.load_policy(path)
    assert "worktree" in str(raised.value)


def test_default_paths_stay_inside_the_suite(tmp_path: Path) -> None:
    repo = tmp_path / "core"
    repo.mkdir()
    core = actions.build_plan("test", ["core"], repo, str(repo), 30).steps[0]
    assert core.argv[-5:] == ("tests/core", "tests/contracts", "tests/gate", "tests/hostrun", "tests/smoke")
    assert "tests/ui" not in core.argv
    ui = actions.build_plan("test", ["ui"], repo, str(repo), 30).steps[0]
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
    assert fake.steps[0].argv[-2:] == ("--socket", str(tmp_path / "rt" / "demo.sock"))
    assert fake.side == "agent-cursor"
    gate = actions.build_plan("gate", ["R0c"], repo, str(repo), 30)
    assert gate.steps[0].argv[1] == f"{repo}/tools/gate/phase_gate.py"


def test_example_policy_loads() -> None:
    policy = server.load_policy(ROOT / "tools" / "hostrun" / "policy.example.toml")
    assert "check" in policy.containers["agent-cursor"].actions
    assert policy.containers["agent-cursor"].worktree.endswith("sp_aconsole_prototype/core")
    assert "gate" in policy.containers["agent-claude"].actions
    assert "gate" not in policy.containers["agent-codex"].actions
    assert "core-run" not in policy.containers["agent-codex"].actions


def test_example_policy_binds_each_container_to_a_worktree() -> None:
    policy = server.load_policy(ROOT / "tools" / "hostrun" / "policy.example.toml")
    assert set(policy.containers) == {"agent-grok", "agent-cursor", "agent-claude", "agent-codex"}
    for name, container in policy.containers.items():
        assert container.worktree.startswith("/"), name
    assert (
        policy.containers["agent-grok"].worktree == "/home/sas/Projects/.aconsole-worktrees/sp_aconsole_prototype/core"
    )
    assert policy.containers["agent-codex"].worktree.endswith("sp_aconsole_prototype/front")
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


def test_check_names_the_tools(running: Running) -> None:
    policy, _holder = running
    _install_check(policy.repo, "#!/bin/sh\nprintf '%s\\n' ruff basedpyright import-linter\n")
    status, body = _post(policy.socket_path, {"action": "check", "args": []})
    assert status == 200
    assert b"ruff\n" in body
    assert b"basedpyright\n" in body
    assert b"__EXIT__ 0\n" in body
    assert body != b"ok\n__EXIT__ 0\n"
    audit = Path(policy.audit_path).read_text(encoding="utf-8")
    assert '"container":"agent-cursor"' in audit
    assert '"action":"check"' in audit


def test_check_sets_the_container_env_for_steps(running: Running, tmp_path: Path) -> None:
    policy, _holder = running
    dump = tmp_path / "env-dump"
    _install_check(policy.repo, f"#!/bin/sh\nprintf '%s' \"$HOSTRUN_CONTAINER\" > {dump}\n")
    status, _body = _post(policy.socket_path, {"action": "check", "args": []})
    assert status == 200
    assert dump.read_text(encoding="utf-8") == "agent-cursor"


def test_check_fails_when_a_tool_fails(running: Running) -> None:
    policy, _holder = running
    _install_check(policy.repo, "#!/bin/sh\nprintf '%s\\n' ruff\nexit 1\n")
    status, body = _post(policy.socket_path, {"action": "check", "args": []})
    assert status == 200
    assert b"ruff\n" in body
    assert b"__EXIT__ 1\n" in body


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
    assert core.steps[0].argv == (
        "/home/sas/.containers/containers/devtools/.cargo/bin/uv",
        "venv",
        "--no-project",
        "--no-python-downloads",
        "--python",
        "3.15t",
        "--allow-existing",
        str(repo / ".venv-core"),
    )
    assert ui.steps[0].argv[5] == "3.15"
    assert "3.15t" not in ui.steps[0].argv
    _ = (repo / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n", encoding="utf-8")
    synced = actions.build_plan("deps-sync", ["core"], repo, str(repo), 30)
    assert synced.steps[1].argv == (
        "/home/sas/.containers/containers/devtools/.cargo/bin/uv",
        "sync",
        "--locked",
        "--no-python-downloads",
        "--python",
        "3.15t",
    )
    assert ("UV_PROJECT_ENVIRONMENT", str(repo / ".venv-core")) in synced.steps[1].env
    synced_ui = actions.build_plan("deps-sync", ["ui"], repo, str(repo), 30)
    assert synced_ui.steps[1].argv == (
        "/home/sas/.containers/containers/devtools/.cargo/bin/uv",
        "sync",
        "--locked",
        "--group",
        "ui",
        "--no-python-downloads",
        "--python",
        "3.15",
    )
    assert ("UV_PROJECT_ENVIRONMENT", str(repo / ".venv-ui")) in synced_ui.steps[1].env
    assert ("PYTHON_GIL", "0") in actions.build_plan("test", ["core", "-k", "smoke"], repo, str(repo), 30).steps[0].env
    ui_test = actions.build_plan("test", ["ui"], repo, str(repo), 30).steps[0]
    assert ui_test.argv[0].endswith("/.venv-ui/bin/python")
    assert ui_test.argv[-1] == "tests/ui"
    assert "tests/core" not in ui_test.argv
    assert ui_test.env == ()
    hostrun = actions.build_plan("test", ["hostrun"], repo, str(repo), 30).steps[0]
    assert hostrun.argv[-1] == "tests/hostrun"
    with pytest.raises(actions.ActionError, match="core or ui"):
        _ = actions.build_plan("deps-sync", ["both"], repo, str(repo), 30)


def test_client_check_and_rejects_shell_args(running: Running, tmp_path: Path) -> None:
    policy, _holder = running
    _install_check(policy.repo, "#!/bin/sh\nprintf '%s\\n' ruff basedpyright\n")
    marker = tmp_path / "client-pwned"
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
    (
        "rust-metadata",
        "crates/core",
        ("cargo", "metadata", "--format-version", "1", "--no-deps", "--manifest-path", "crates/core/Cargo.toml"),
    ),
    ("csharp", "src/Cs", ("dotnet", "run", "--project", "tools/csgraph", "--", "src/Cs")),
]


@pytest.mark.parametrize(("extractor", "path", "tail"), DEVTOOLS_CASES)
def test_devtools_extractor_argv(tmp_path: Path, extractor: str, path: str, tail: tuple[str, ...]) -> None:
    repo = str(tmp_path)
    plan = actions.build_plan("devtools", [extractor, path], tmp_path, repo, 30)
    assert plan.steps[0].argv == ("podman", "start", "devtools")
    assert plan.steps[1].argv == ("podman", "exec", "--workdir", repo, "devtools", *tail)


def test_devtools_rust_metadata_appends_cargo_toml_after_a_trailing_slash(tmp_path: Path) -> None:
    repo = str(tmp_path)
    plan = actions.build_plan("devtools", ["rust-metadata", "crates/core/"], tmp_path, repo, 30)
    assert plan.steps[-1].argv[-1] == "crates/core/Cargo.toml"


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
