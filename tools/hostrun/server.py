"""hostrun server: HTTP/1.1 over a Unix socket, named actions only.

The process is the host Python 3.12 stdlib. It refuses to run as root and
never starts a shell. Callers are identified with SO_PEERCRED.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import select
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import actions

_MAX_BODY = 65536
_LIBPOD_ID = re.compile(r"libpod-([0-9a-fA-F]{64})\.scope")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_ENV_ALLOW = frozenset(
    {
        "DISPLAY",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "TZ",
        "TMPDIR",
        "UV_CACHE_DIR",
        "UV_PYTHON_INSTALL_DIR",
        "WAYLAND_DISPLAY",
        "XDG_CURRENT_DESKTOP",
        "XDG_RUNTIME_DIR",
    }
)
_FIXED_PATH = "/home/sas/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_EXAMPLE = Path(__file__).with_name("policy.example.toml")
_USER_POLICY = Path("/home/sas/.config/aconsole-hostrun/policy.toml")


class PolicyError(Exception):
    """The policy file cannot be loaded."""


@dataclass(frozen=True, kw_only=True)
class ContainerPolicy:
    actions: frozenset[str]
    worktree: str


@dataclass(frozen=True, kw_only=True)
class Policy:
    projects_root: str
    repo: str
    socket_path: str
    audit_path: str
    state_dir: str
    output_limit_bytes: int
    default_timeout_s: float
    timeouts: Mapping[str, float]
    containers: Mapping[str, ContainerPolicy]

    def timeout_for(self, action: str) -> float:
        return self.timeouts.get(action, self.default_timeout_s)

    def allows(self, container: str, action: str) -> bool:
        spec = self.containers.get(container)
        return spec is not None and action in spec.actions


@dataclass(frozen=True, kw_only=True)
class Request:
    action: str
    args: tuple[str, ...]
    cwd: str | None


def ensure_not_root(euid: Callable[[], int] = os.geteuid) -> None:
    if euid() == 0:
        sys.stderr.write("hostrun refuses to run as root\n")
        raise SystemExit(1)


def container_id_from_cgroup(text: str) -> str | None:
    match = _LIBPOD_ID.search(text)
    if match is None:
        return None
    return match.group(1)


def normalize_podman_name(raw: str) -> str | None:
    name = raw.strip()
    name = name.removeprefix("/")
    if _NAME.fullmatch(name) is None:
        return None
    return name


def peer_pid(sock: socket.socket) -> int:
    raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    pid, _uid, _gid = struct.unpack("3i", raw)
    if pid <= 0:
        raise OSError("missing peer pid")
    return pid


def read_cgroup(pid: int) -> str:
    return Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8")


def inspect_name(container_id: str) -> str | None:
    if re.fullmatch(r"[0-9a-fA-F]{64}", container_id) is None:
        return None
    try:
        completed = subprocess.run(
            ["podman", "inspect", "--format", "{{.Name}}", container_id],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return normalize_podman_name(completed.stdout)


def resolve_container_name(pid: int) -> str | None:
    try:
        text = read_cgroup(pid)
    except OSError:
        return None
    container_id = container_id_from_cgroup(text)
    if container_id is None:
        return None
    return inspect_name(container_id)


def read_toml(path: Path) -> object:
    with path.open("rb") as handle:
        return tomllib.load(handle)  # pyright: ignore[reportAny] -- tomllib.load is typed as Any


def _plain(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise PolicyError("policy keys must be strings")
            out[key] = _plain(item)
        return out
    raise PolicyError("unsupported policy value")


def _table(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PolicyError(f"{label} must be a table")
    return value


def _text(table: dict[str, object], key: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or value == "":
        raise PolicyError(f"{key} must be a string")
    return value


def _positive_int(table: dict[str, object], key: str) -> int:
    value = table.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise PolicyError(f"{key} must be a positive integer")
    return value


def _positive_float(table: dict[str, object], key: str) -> float:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise PolicyError(f"{key} must be a positive number")
    return float(value)


def load_policy(path: Path) -> Policy:
    raw = _plain(read_toml(path))
    root = _table(raw, "policy")
    projects_root = _text(root, "projects_root")
    repo = _text(root, "repo")
    if ".." in Path(repo).parts or not Path(repo).resolve().is_relative_to(Path(projects_root).resolve()):
        raise PolicyError("repo must stay inside projects_root")
    timeouts_raw = _table(root.get("timeouts"), "timeouts")
    timeouts: dict[str, float] = {}
    for name, item in timeouts_raw.items():
        if name not in actions.KNOWN_ACTIONS:
            raise PolicyError(f"unknown timeout action {name}")
        if isinstance(item, bool) or not isinstance(item, int | float) or item <= 0:
            raise PolicyError(f"timeout for {name} must be a positive number")
        timeouts[name] = float(item)
    containers_raw = _table(root.get("containers"), "containers")
    containers: dict[str, frozenset[str]] = {}
    for name, spec in containers_raw.items():
        if _NAME.fullmatch(name) is None:
            raise PolicyError(f"bad container name {name}")
        table = _table(spec, name)
        listed = table.get("actions")
        if not isinstance(listed, list) or not listed:
            raise PolicyError(f"{name} needs actions")
        names: list[str] = []
        for item in listed:
            if not isinstance(item, str) or item not in actions.KNOWN_ACTIONS:
                raise PolicyError(f"{name} lists an unknown action")
            names.append(item)
        worktree = table.get("worktree")
        if not isinstance(worktree, str) or worktree == "" or ".." in Path(worktree).parts:
            raise PolicyError(f"{name} needs worktree")
        containers[name] = ContainerPolicy(actions=frozenset(names), worktree=worktree)
    return Policy(
        projects_root=projects_root,
        repo=repo,
        socket_path=_text(root, "socket_path"),
        audit_path=_text(root, "audit_path"),
        state_dir=_text(root, "state_dir"),
        output_limit_bytes=_positive_int(root, "output_limit_bytes"),
        default_timeout_s=_positive_float(root, "default_timeout_s"),
        timeouts=timeouts,
        containers=containers,
    )


def policy_path() -> Path:
    override = os.environ.get("HOSTRUN_POLICY")
    if override:
        return Path(override)
    if _USER_POLICY.is_file():
        return _USER_POLICY
    return _EXAMPLE


class _Parser:
    def __init__(self, text: str) -> None:
        self._text = text
        self._i = 0
        self._n = len(text)
        self._depth = 0

    def parse(self) -> object:
        value = self._value()
        self._skip()
        if self._i != self._n:
            raise ValueError("trailing json")
        return value

    def _skip(self) -> None:
        while self._i < self._n and self._text[self._i] in " \t\r\n":
            self._i += 1

    def _peek(self) -> str:
        self._skip()
        if self._i >= self._n:
            raise ValueError("truncated json")
        return self._text[self._i]

    def _value(self) -> object:
        char = self._peek()
        if char == "{":
            return self._object()
        if char == "[":
            return self._array()
        if char == '"':
            return self._string()
        if char == "t":
            return self._literal("true", True)
        if char == "f":
            return self._literal("false", False)
        if char == "n":
            return self._literal("null", None)
        if char == "-" or char.isdigit():
            return self._number()
        raise ValueError("bad json value")

    def _literal(self, token: str, value: object) -> object:
        self._skip()
        if self._text[self._i : self._i + len(token)] != token:
            raise ValueError("bad literal")
        self._i += len(token)
        return value

    def _object(self) -> dict[str, object]:
        self._enter()
        self._i += 1
        out: dict[str, object] = {}
        if self._peek() == "}":
            self._i += 1
            self._depth -= 1
            return out
        while True:
            if self._peek() != '"':
                raise ValueError("expected key")
            key = self._string()
            if self._peek() != ":":
                raise ValueError("expected colon")
            self._i += 1
            out[key] = self._value()
            char = self._peek()
            self._i += 1
            if char == "}":
                self._depth -= 1
                return out
            if char != ",":
                raise ValueError("expected comma")

    def _array(self) -> list[object]:
        self._enter()
        self._i += 1
        out: list[object] = []
        if self._peek() == "]":
            self._i += 1
            self._depth -= 1
            return out
        while True:
            out.append(self._value())
            char = self._peek()
            self._i += 1
            if char == "]":
                self._depth -= 1
                return out
            if char != ",":
                raise ValueError("expected comma")

    def _enter(self) -> None:
        self._depth += 1
        if self._depth > 8:
            raise ValueError("json too deep")

    def _string(self) -> str:
        self._skip()
        if self._text[self._i] != '"':
            raise ValueError("expected string")
        self._i += 1
        chars: list[str] = []
        while self._i < self._n:
            char = self._text[self._i]
            self._i += 1
            if char == '"':
                if len(chars) > 8192:
                    raise ValueError("string too long")
                return "".join(chars)
            if char == "\\":
                chars.append(self._escape())
                continue
            if ord(char) < 32:
                raise ValueError("raw control character")
            chars.append(char)
        raise ValueError("unterminated string")

    def _escape(self) -> str:
        if self._i >= self._n:
            raise ValueError("bad escape")
        char = self._text[self._i]
        self._i += 1
        mapping = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
        if char in mapping:
            return mapping[char]
        if char != "u":
            raise ValueError("bad escape")
        hex_digits = self._text[self._i : self._i + 4]
        self._i += 4
        if len(hex_digits) != 4 or any(digit not in "0123456789abcdefABCDEF" for digit in hex_digits):
            raise ValueError("bad unicode escape")
        code = int(hex_digits, 16)
        if 0xD800 <= code <= 0xDFFF:
            raise ValueError("surrogate escape")
        return chr(code)

    def _number(self) -> int | float:
        self._skip()
        start = self._i
        if self._text[self._i] == "-":
            self._i += 1
        if self._i >= self._n or not self._text[self._i].isdigit():
            raise ValueError("bad number")
        if self._text[self._i] == "0":
            self._i += 1
        else:
            while self._i < self._n and self._text[self._i].isdigit():
                self._i += 1
        fractional = False
        if self._i < self._n and self._text[self._i] == ".":
            fractional = True
            self._i += 1
            if self._i >= self._n or not self._text[self._i].isdigit():
                raise ValueError("bad number")
            while self._i < self._n and self._text[self._i].isdigit():
                self._i += 1
        if self._i < self._n and self._text[self._i] in "eE":
            fractional = True
            self._i += 1
            if self._i < self._n and self._text[self._i] in "+-":
                self._i += 1
            if self._i >= self._n or not self._text[self._i].isdigit():
                raise ValueError("bad number")
            while self._i < self._n and self._text[self._i].isdigit():
                self._i += 1
        token = self._text[start : self._i]
        if fractional:
            return float(token)
        return int(token)


def parse_request(raw: bytes) -> Request:
    try:
        value = _Parser(raw.decode("utf-8")).parse()
    except (UnicodeDecodeError, ValueError) as exc:
        raise actions.ActionError(400, "malformed json") from exc
    if not isinstance(value, dict):
        raise actions.ActionError(400, "body must be an object")
    action = value.get("action")
    args = value.get("args", [])
    cwd = value.get("cwd")
    if not isinstance(action, str) or action == "" or len(action) > 64:
        raise actions.ActionError(400, "action must be a string")
    if not isinstance(args, list) or len(args) > 64:
        raise actions.ActionError(400, "args must be a list of strings")
    clean: list[str] = []
    for item in args:
        if not isinstance(item, str) or len(item) > 4096 or "\x00" in item:
            raise actions.ActionError(400, "args must be a list of strings")
        clean.append(item)
    if cwd is not None and not isinstance(cwd, str):
        raise actions.ActionError(400, "cwd must be a string")
    return Request(action=action, args=tuple(clean), cwd=cwd)


def base_env() -> dict[str, str]:
    env: dict[str, str] = {
        "PATH": _FIXED_PATH,
        "HOME": "/home/sas",
        "USER": "sas",
        "LOGNAME": "sas",
    }
    for key in _ENV_ALLOW:
        value = os.environ.get(key)
        if value:
            env[key] = value
    env.setdefault("LANG", "C.UTF-8")
    return env


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return


def _posix_exit(code: int) -> int:
    if code < 0:
        return min(255, 128 - code)
    return min(code, 255)


def execute_argv(
    argv: tuple[str, ...],
    *,
    cwd: str,
    env: dict[str, str],
    deadline: float,
    output_limit: int,
    written: list[int],
    write: Callable[[bytes], None],
) -> int:
    """Run a fixed argv. `shell` stays false. Output is capped and timed."""
    try:
        proc = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            shell=False,
        )
    except FileNotFoundError:
        write(f"hostrun: command not found: {argv[0]}\n".encode())
        return 127
    except OSError as exc:
        detail = exc.strerror or "cannot start command"
        write(f"hostrun: {detail}\n".encode())
        return 127
    stdout = proc.stdout
    if stdout is None:
        _kill_group(proc)
        write(b"hostrun: no stdout\n")
        return 127
    fd = stdout.fileno()
    os.set_blocking(fd, False)
    timed_out = False
    limited = False
    try:
        while True:
            now = time.monotonic()
            if now >= deadline:
                timed_out = True
                break
            readable, _, _ = select.select([fd], [], [], min(0.05, deadline - now))
            if not readable:
                if proc.poll() is not None:
                    continue_read = True
                    while continue_read:
                        try:
                            chunk = os.read(fd, 65536)
                        except BlockingIOError:
                            continue_read = False
                            break
                        if chunk == b"":
                            continue_read = False
                            break
                        if not _take(chunk, written, output_limit, write):
                            limited = True
                            continue_read = False
                    break
                continue
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                continue
            if chunk == b"":
                break
            if not _take(chunk, written, output_limit, write):
                limited = True
                break
        if timed_out or limited:
            _kill_group(proc)
        else:
            return _posix_exit(proc.wait(timeout=5))
    finally:
        stdout.close()
    if timed_out:
        write(b"hostrun: timeout\n")
        return 124
    write(b"hostrun: output limit exceeded\n")
    return 125


def _take(chunk: bytes, written: list[int], output_limit: int, write: Callable[[bytes], None]) -> bool:
    room = output_limit - written[0]
    if room <= 0 or len(chunk) > room:
        if room > 0:
            write(chunk[:room])
            written[0] += room
        return False
    write(chunk)
    written[0] += len(chunk)
    return True


def _cmdline(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    parts = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
    return parts


class Application:
    def __init__(self, policy: Policy, *, resolve_peer: Callable[[int], str | None] = resolve_container_name) -> None:
        self.policy = policy
        self.resolve_peer = resolve_peer
        self._audit_lock = threading.Lock()

    def handler_class(self) -> type[BaseHTTPRequestHandler]:
        app = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_POST(self) -> None:
                app.serve_http(self)

            def do_GET(self) -> None:
                app.send_error(self, 405, "method not allowed")

            def log_message(self, format: str, *args: object) -> None:
                _ = (format, args)

        return Handler

    def send_error(self, handler: BaseHTTPRequestHandler, status: int, message: str) -> None:
        body = f"{message}\n".encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(body)

    def serve_http(self, handler: BaseHTTPRequestHandler) -> None:
        started = time.monotonic()
        peer = 0
        container = ""
        action = ""
        args: tuple[str, ...] = ()
        cwd_text = ""
        argv: tuple[str, ...] = ()
        status = 500
        exit_code = 1
        error = ""
        headers_sent = False
        try:
            connection = handler.connection
            if not isinstance(connection, socket.socket):
                raise actions.ActionError(400, "missing socket")
            peer = peer_pid(connection)
            if handler.path.split("?", 1)[0] != "/run":
                raise actions.ActionError(404, "not found")
            length_text = handler.headers.get("Content-Length")
            if length_text is None or not length_text.isdigit():
                raise actions.ActionError(400, "content-length required")
            length = int(length_text)
            if length > _MAX_BODY:
                raise actions.ActionError(413, "body too large")
            raw = handler.rfile.read(length)
            request = parse_request(raw)
            action = request.action
            args = request.args
            container_name = self.resolve_peer(peer)
            container = container_name or ""
            if container_name is None:
                raise actions.ActionError(403, "unknown container: peer is not an identified podman container")
            if container_name not in self.policy.containers:
                raise actions.ActionError(403, f"unknown container: {container_name} is not in policy")
            if request.action not in actions.KNOWN_ACTIONS:
                raise actions.ActionError(400, "unknown action")
            if not self.policy.allows(container_name, request.action):
                raise actions.ActionError(403, "action not permitted")
            spec = self.policy.containers[container_name]
            cwd_value = request.cwd if request.cwd is not None else spec.worktree
            cwd_text = cwd_value
            cwd = actions.validate_cwd(cwd_value, self.policy.projects_root)
            runtime = str(Path(self.policy.state_dir) / container_name)
            plan = actions.build_plan(
                request.action,
                list(request.args),
                cwd,
                spec.worktree,
                self.policy.timeout_for(request.action),
                runtime_dir=runtime,
                side=container_name,
            )
            argv = tuple(part for step in plan.steps for part in step.argv)
            handler.send_response(200)
            handler.send_header("Content-Type", "text/plain; charset=utf-8")
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            headers_sent = True
            ended_nl = True

            def write(data: bytes) -> None:
                nonlocal ended_nl
                if data:
                    ended_nl = data.endswith(b"\n")
                try:
                    handler.wfile.write(data)
                    handler.wfile.flush()
                except (BrokenPipeError, ConnectionResetError) as exc:
                    raise _ClientGone from exc

            exit_code = self.run_plan(plan, cwd=str(cwd), write=write, container=container)
            if not ended_nl:
                write(b"\n")
            write(f"__EXIT__ {exit_code}\n".encode())
            status = 200
        except _ClientGone:
            status = 200
            error = "client gone"
        except actions.ActionError as exc:
            status = exc.status
            exit_code = exc.status
            error = exc.message
            if not headers_sent:
                self.send_error(handler, exc.status, exc.message)
        except Exception as exc:
            status = 500
            exit_code = 1
            error = exc.__class__.__name__
            if not headers_sent:
                self.send_error(handler, 500, "internal error")
        finally:
            self.audit(
                container=container,
                peer_pid=peer,
                action=action,
                args=args,
                cwd=cwd_text,
                argv=argv,
                exit_code=exit_code,
                duration_ms=int((time.monotonic() - started) * 1000),
                error=error,
                status=status,
            )

    def run_plan(self, plan: actions.Plan, *, cwd: str, write: Callable[[bytes], None], container: str = "") -> int:
        if plan.mode == "check":
            script = str(Path(plan.repo) / "tools" / "check.sh")
            env = base_env()
            if container:
                env["HOSTRUN_CONTAINER"] = container
            return execute_argv(
                ("sh", script),
                cwd=cwd,
                env=env,
                deadline=time.monotonic() + plan.timeout_s,
                output_limit=self.policy.output_limit_bytes,
                written=[0],
                write=write,
            )
        if plan.mode == "fake-start":
            return self.start_fake(plan, cwd=cwd, write=write, container=container)
        if plan.mode == "fake-stop":
            return self.stop_fake(plan, write=write)
        deadline = time.monotonic() + plan.timeout_s
        written = [0]
        code = 0
        for step in plan.steps:
            env = base_env()
            env.update(dict(step.env))
            if container:
                env["HOSTRUN_CONTAINER"] = container
            code = execute_argv(
                step.argv,
                cwd=cwd,
                env=env,
                deadline=deadline,
                output_limit=self.policy.output_limit_bytes,
                written=written,
                write=write,
            )
            if code != 0:
                return code
        return code

    def start_fake(self, plan: actions.Plan, *, cwd: str, write: Callable[[bytes], None], container: str = "") -> int:
        step = plan.steps[0]
        directory = Path(self.policy.state_dir) / "fake-core"
        directory.mkdir(parents=True, exist_ok=True)
        pid_path = directory / f"{plan.side}-{plan.scenario}.pid"
        existing = _read_pid(pid_path)
        if existing is not None and _cmdline(existing) == list(step.argv):
            write(f"already running {existing}\n".encode())
            return 0
        env = base_env()
        env.update(dict(step.env))
        if container:
            env["HOSTRUN_CONTAINER"] = container
        log = (directory / f"{plan.scenario}.log").open("ab")
        try:
            proc = subprocess.Popen(
                list(step.argv),
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                shell=False,
            )
        except FileNotFoundError:
            write(f"hostrun: command not found: {step.argv[0]}\n".encode())
            return 127
        except OSError as exc:
            detail = exc.strerror or "cannot start command"
            write(f"hostrun: {detail}\n".encode())
            return 127
        finally:
            log.close()
        pid_path.write_text(f"{proc.pid}\n", encoding="utf-8")
        socket_path = Path(plan.socket_path)
        if str(socket_path):
            socket_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if plan.socket_path and Path(plan.socket_path).exists():
                write(f"started {proc.pid}\n".encode())
                return 0
            if proc.poll() is not None:
                write(f"exited {proc.returncode}\n".encode())
                return 1
            time.sleep(0.05)
        write(b"socket did not appear\n")
        return 1

    def stop_fake(self, plan: actions.Plan, *, write: Callable[[bytes], None]) -> int:
        step = plan.steps[0]
        pid_path = Path(self.policy.state_dir) / "fake-core" / f"{plan.side}-{plan.scenario}.pid"
        pid = _read_pid(pid_path)
        if pid is None:
            write(b"not running\n")
            return 1
        if _cmdline(pid) != list(step.argv):
            write(b"pid does not match the fake core\n")
            return 1
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if _cmdline(pid) is None:
                break
            time.sleep(0.05)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        pid_path.unlink(missing_ok=True)
        write(b"stopped\n")
        return 0

    def audit(
        self,
        *,
        container: str,
        peer_pid: int,
        action: str,
        args: tuple[str, ...],
        cwd: str,
        argv: tuple[str, ...],
        exit_code: int,
        duration_ms: int,
        error: str,
        status: int,
    ) -> None:
        row = {
            "ts": datetime.now(UTC).isoformat(),
            "container": container,
            "peer_pid": peer_pid,
            "action": action,
            "args": list(args),
            "cwd": cwd,
            "argv": list(argv),
            "exit": exit_code,
            "duration_ms": duration_ms,
            "error": error,
            "http_status": status,
        }
        try:
            line = json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n"
        except (TypeError, ValueError):
            return
        path = Path(self.policy.audit_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_lock, path.open("a", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                handle.write(line)
                handle.flush()
        except OSError:
            line = ""
        journal_send(
            error or action or "hostrun",
            CONTAINER=container,
            ACTION=action,
            EXIT=str(exit_code),
            STATUS=str(status),
        )


class _ClientGone(Exception):
    """The UI closed the socket while a command was still running."""


def _read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.isdigit():
        return None
    return int(text)


def journal_send(message: str, **fields: str) -> None:
    path = "/run/systemd/journal/socket"
    if not os.path.exists(path):
        return
    payload = bytearray()
    items = {"MESSAGE": message, "SYSLOG_IDENTIFIER": "aconsole-hostrun", "PRIORITY": "6", **fields}
    for key, value in items.items():
        encoded = value.encode("utf-8")
        if b"\n" in encoded:
            payload.extend(f"{key}\n".encode())
            payload.extend(struct.pack("<Q", len(encoded)))
            payload.extend(encoded)
            payload.extend(b"\n")
        else:
            payload.extend(f"{key}={value}\n".encode())
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.sendto(bytes(payload), path)
    except OSError:
        return
    finally:
        sock.close()


class ThreadingUnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self) -> None:
        address = self.server_address
        if isinstance(address, str) and os.path.exists(address):
            os.unlink(address)
        super().server_bind()
        if isinstance(address, str):
            os.chmod(address, 0o600)

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        connection, _address = super().get_request()
        return connection, ("unix", 0)


def prepare_paths(policy: Policy) -> None:
    socket_dir = Path(policy.socket_path).parent
    socket_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(socket_dir, 0o700)
    Path(policy.audit_path).parent.mkdir(parents=True, exist_ok=True)
    Path(policy.state_dir).mkdir(parents=True, exist_ok=True)
    bus_none = socket_dir / "bus-none"
    if not bus_none.exists():
        bus_none.write_bytes(b"")


def serve(policy: Policy, *, resolve_peer: Callable[[int], str | None] = resolve_container_name) -> None:
    prepare_paths(policy)
    app = Application(policy, resolve_peer=resolve_peer)
    httpd = ThreadingUnixHTTPServer(policy.socket_path, app.handler_class())
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


def main() -> None:
    ensure_not_root()
    serve(load_policy(policy_path()))


if __name__ == "__main__":
    main()
