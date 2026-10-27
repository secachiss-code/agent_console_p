"""Named hostrun actions. Each action becomes a fixed argv. No shell."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

KNOWN_ACTIONS: frozenset[str] = frozenset(
    {
        "check",
        "deps-sync",
        "test",
        "ui-snapshot",
        "ui-run",
        "fake-core",
        "core-run",
        "podman-ps",
        "distrobox-dry-run",
        "git",
        "gate",
        "conformance",
        "devtools",
    }
)

# Real uv binary. ~/.local/bin/uv is a distrobox-enter wrapper and must not run here.
_UV = "/home/sas/.containers/containers/devtools/.cargo/bin/uv"

_SCENARIO = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
# H1, R0b, A0.0r, I0 and frontend ids. One trailing letter is a part (A1.1b), two are not.
_TASK = re.compile(
    r"(?:A[0-9]{1,2}(?:\.[0-9]{1,2})?[a-z]?|H[0-9]{1,2}|R[0-9]{1,2}[a-z]?|I[0-9]{1,2}|B[0-9]{1,2}[a-z]?)\Z"
)
_CONTAINER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_PYTEST_K = re.compile(r"[A-Za-z0-9_ (),:-]{1,200}\Z")
_PYTEST_PATH = re.compile(r"[A-Za-z0-9_./-]{1,512}\Z")
_SNAPSHOT_OUT = re.compile(r"docs/ui/[A-Za-z0-9_./-]+\.png\Z")
_SIZE = re.compile(r"[0-9]{2,5}x[0-9]{2,5}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
# The MCP server under test listens on loopback only (MCP_TOOLS.md §5).
_MCP_URL = re.compile(r"http://127\.0\.0\.1:(?P<port>[0-9]{4,5})/mcp\Z")
_REPO_PATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,511}\Z")

# Container with node, dotnet, rust and python (PLAN §4). It mounts $HOME, so worktree paths are the same there.
_DEVTOOLS = "devtools"
# 0.2.0-alpha.12 is the first release that knows the 2026-07-28 requirement set; 0.1.16 (latest) stops at 2025-11-25.
_CONFORMANCE = "@modelcontextprotocol/conformance@0.2.0-alpha.12"
_MCP_REVISION = "2026-07-28"
# Extractors for the code graph (A8.1). The path argument is appended; each tool is pinned to the version in PLAN §8.
# rust-modules (cargo-modules 0.28) is added by A8.3 once its flags are checked against the installed version.
_EXTRACTORS: dict[str, tuple[str, ...]] = {
    "python": ("python3", "tools/codegraph/extract_python.py"),
    "ts": ("npx", "--yes", "dependency-cruiser@18.7.0", "--output-type", "json"),
    "rust-metadata": ("cargo", "metadata", "--format-version", "1", "--no-deps", "--manifest-path"),
    "csharp": ("dotnet", "run", "--project", "tools/csgraph", "--"),
}


class ActionError(Exception):
    """A request that must be rejected before any process is started."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True, kw_only=True)
class Step:
    argv: tuple[str, ...]
    env: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, kw_only=True)
class Plan:
    """What the server is allowed to run for one request."""

    mode: str
    steps: tuple[Step, ...]
    timeout_s: float
    scenario: str = ""
    repo: str = ""
    socket_path: str = ""
    side: str = ""


def validate_cwd(cwd: str, projects_root: str) -> Path:
    """Accept only a real directory inside the projects root.

    A `..` segment is rejected before resolution. A symlink whose target
    leaves the projects root is rejected after resolution.
    """
    if cwd == "" or "\x00" in cwd or len(cwd) > 4096:
        raise ActionError(400, "cwd is not allowed")
    if not cwd.startswith("/"):
        raise ActionError(400, "cwd must be absolute")
    candidate = Path(cwd)
    if ".." in candidate.parts:
        raise ActionError(400, "cwd contains ..")
    root = Path(projects_root).resolve()
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise ActionError(400, "cwd is not allowed") from exc
    if not resolved.is_relative_to(root):
        raise ActionError(400, "cwd escapes projects")
    if not resolved.is_dir():
        raise ActionError(400, "cwd is not a directory")
    return resolved


def build_plan(
    action: str,
    args: list[str],
    cwd: Path,
    repo: str,
    timeout_s: float,
    *,
    runtime_dir: str = "",
    side: str = "",
) -> Plan:
    """Map a named action onto fixed steps. `repo` is the container worktree."""
    if action not in KNOWN_ACTIONS:
        raise ActionError(400, "unknown action")
    if action == "check":
        _no_args(args, "check")
        return Plan(mode="check", steps=(), timeout_s=timeout_s, repo=repo, side=side)
    if action == "deps-sync":
        return Plan(mode="exec", steps=_deps_sync(args, repo), timeout_s=timeout_s)
    if action == "test":
        return Plan(mode="exec", steps=(_test(args, repo),), timeout_s=timeout_s)
    if action == "ui-snapshot":
        return Plan(mode="exec", steps=(_ui_snapshot(args, repo),), timeout_s=timeout_s)
    if action == "ui-run":
        return Plan(mode="exec", steps=(_ui_run(args, repo),), timeout_s=timeout_s)
    if action == "fake-core":
        return _fake_core(args, repo, timeout_s, runtime_dir or f"{repo}/.fake-runtime", side)
    if action == "core-run":
        _no_args(args, "core-run")
        return Plan(
            mode="exec",
            steps=(Step(argv=(_core_python(repo), "-m", "aconsole.core"), env=_gil_off()),),
            timeout_s=timeout_s,
        )
    if action == "podman-ps":
        _no_args(args, "podman-ps")
        return Plan(
            mode="exec",
            steps=(Step(argv=("podman", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.ID}}")),),
            timeout_s=timeout_s,
        )
    if action == "distrobox-dry-run":
        return Plan(mode="exec", steps=(_distrobox(args),), timeout_s=timeout_s)
    if action == "git":
        return Plan(mode="exec", steps=(_git(args, cwd),), timeout_s=timeout_s)
    if action == "gate":
        return Plan(mode="exec", steps=(_gate(args, repo),), timeout_s=timeout_s, repo=repo, side=side)
    if action == "conformance":
        return Plan(mode="exec", steps=(_conformance(args, repo),), timeout_s=timeout_s)
    if action == "devtools":
        return Plan(mode="exec", steps=(_devtools(args, repo),), timeout_s=timeout_s)
    raise ActionError(400, "unknown action")


def _no_args(args: list[str], action: str) -> None:
    if args:
        raise ActionError(400, f"{action} does not take arguments")


def _gil_off() -> tuple[tuple[str, str], ...]:
    return (("PYTHON_GIL", "0"),)


def _core_python(repo: str) -> str:
    return f"{repo}/.venv-core/bin/python"


def _ui_python(repo: str) -> str:
    return f"{repo}/.venv-ui/bin/python"


def _deps_sync(args: list[str], repo: str) -> tuple[Step, ...]:
    """Create the venv with the interpreter profile, then sync if a project exists.

    Multithreaded core: `uv venv --python 3.15t` → `.venv-core`.
    UI and ordinary apps: `uv venv --python 3.15` → `.venv-ui`.
    """
    if len(args) != 1 or args[0] not in {"core", "ui"}:
        raise ActionError(400, "deps-sync expects core or ui")
    if args[0] == "core":
        spec = "3.15t"
        venv = f"{repo}/.venv-core"
    else:
        spec = "3.15"
        venv = f"{repo}/.venv-ui"
    create = Step(
        argv=(
            _UV,
            "venv",
            "--no-project",
            "--no-python-downloads",
            "--python",
            spec,
            "--allow-existing",
            venv,
        )
    )
    project = Path(repo) / "pyproject.toml"
    if not project.is_file():
        return (create,)
    sync_argv = [_UV, "sync", "--locked"]
    if args[0] == "ui":
        sync_argv.extend(["--group", "ui"])
    sync_argv.extend(["--no-python-downloads", "--python", spec])
    sync = Step(
        argv=tuple(sync_argv),
        env=(("UV_PROJECT_ENVIRONMENT", venv),),
    )
    return (create, sync)


_SUITES: dict[str, tuple[str, ...]] = {
    # tests/smoke prints the interpreter and its GIL state for each venv (A0.1).
    "core": ("tests/core", "tests/contracts", "tests/gate", "tests/hostrun", "tests/smoke"),
    "ui": ("tests/smoke", "tests/ui"),
    "gui": ("tests/gui",),
    "contracts": ("tests/contracts",),
    "hostrun": ("tests/hostrun",),
    "gate": ("tests/gate",),
}


def _test(args: list[str], repo: str) -> Step:
    if not args or args[0] not in _SUITES:
        raise ActionError(400, "test expects core, ui, gui, contracts, hostrun, or gate")
    suite = args[0]
    rest = args[1:]
    if suite in {"ui", "gui"}:
        python = _ui_python(repo)
        env: tuple[tuple[str, str], ...] = ()
    else:
        python = _core_python(repo)
        env = _gil_off()
    command: list[str] = [python, "-m", "pytest", "-q"]
    if rest[:1] == ["-k"]:
        if len(rest) < 2 or _PYTEST_K.fullmatch(rest[1]) is None:
            raise ActionError(400, "pytest -k expression is not allowed")
        command.extend(["-k", rest[1]])
        rest = rest[2:]
    if len(rest) > 32:
        raise ActionError(400, "too many test paths")
    paths = tuple(_pytest_path(path) for path in rest) if rest else _SUITES[suite]
    command.extend(paths)
    return Step(argv=tuple(command), env=env)


def _pytest_path(path: str) -> str:
    if path.startswith("/") or _PYTEST_PATH.fullmatch(path) is None:
        raise ActionError(400, "test path is not allowed")
    if any(part in {"", ".", ".."} for part in path.split("/")):
        raise ActionError(400, "test path is not allowed")
    return path


def _ui_snapshot(args: list[str], repo: str) -> Step:
    python = _ui_python(repo)
    tool = f"{repo}/tools/ui_snapshot.py"
    if args == ["--spike"]:
        return Step(argv=(python, tool, "--spike"))
    if (
        len(args) == 10
        and args[0] == "--scenario"
        and args[2] == "--view"
        and args[4] == "--theme"
        and args[6] == "--size"
        and args[8] == "--out"
    ):
        scenario, view, theme, size, out = args[1], args[3], args[5], args[7], args[9]
        if _TOKEN.fullmatch(scenario) is None or _TOKEN.fullmatch(view) is None:
            raise ActionError(400, "snapshot name is not allowed")
        if theme not in {"light", "dark"}:
            raise ActionError(400, "snapshot theme must be light or dark")
        if _SIZE.fullmatch(size) is None:
            raise ActionError(400, "snapshot size is not allowed")
        if _SNAPSHOT_OUT.fullmatch(out) is None or ".." in out.split("/"):
            raise ActionError(400, "snapshot output must be a png under docs/ui/")
        return Step(
            argv=(python, tool, "--scenario", scenario, "--view", view, "--theme", theme, "--size", size, "--out", out)
        )
    raise ActionError(400, "ui-snapshot arguments are not allowed")


def _ui_run(args: list[str], repo: str) -> Step:
    python = _ui_python(repo)
    if args == ["--spike"]:
        return Step(argv=(python, "-m", "aconsole", "--spike"))
    if len(args) == 2 and args[0] == "--fake" and _SCENARIO.fullmatch(args[1]) is not None:
        return Step(argv=(python, "-m", "aconsole", "--fake", args[1]))
    raise ActionError(400, "ui-run arguments are not allowed")


def _fake_core(args: list[str], repo: str, timeout_s: float, runtime_dir: str, side: str) -> Plan:
    if len(args) != 2 or args[0] not in {"start", "stop"} or _SCENARIO.fullmatch(args[1]) is None:
        raise ActionError(400, "fake-core expects start or stop and a scenario")
    socket_path = f"{runtime_dir}/{args[1]}.sock"
    argv = (_core_python(repo), "-m", "aconsole.core", "--fake", args[1], "--socket", socket_path)
    mode = "fake-start" if args[0] == "start" else "fake-stop"
    return Plan(
        mode=mode,
        steps=(Step(argv=argv, env=_gil_off()),),
        timeout_s=timeout_s,
        scenario=args[1],
        repo=repo,
        socket_path=socket_path,
        side=side,
    )


def _distrobox(args: list[str]) -> Step:
    if len(args) != 1 or _CONTAINER.fullmatch(args[0]) is None:
        raise ActionError(400, "distrobox-dry-run expects one container name")
    return Step(argv=("distrobox", "enter", "--dry-run", args[0]))


def _git(args: list[str], cwd: Path) -> Step:
    base = ("git", "-C", str(cwd))
    if args == ["status"]:
        return Step(argv=base + ("status",))
    if args == ["diff"]:
        return Step(argv=base + ("diff",))
    if args == ["log"]:
        return Step(argv=base + ("log",))
    if args == ["worktree", "list"]:
        return Step(argv=base + ("worktree", "list"))
    if args == ["rev-parse", "HEAD"]:
        return Step(argv=base + ("rev-parse", "HEAD"))
    raise ActionError(400, "git arguments are not allowed")


def _gate(args: list[str], repo: str) -> Step:
    if args == ["--audit", "core"]:
        return Step(
            argv=(_core_python(repo), f"{repo}/tools/gate/phase_gate.py", "--audit", "core"),
            env=_gil_off(),
        )
    if len(args) != 1 or _TASK.fullmatch(args[0]) is None:
        raise ActionError(400, "gate expects a task id or --audit core")
    return Step(
        argv=(_core_python(repo), f"{repo}/tools/gate/phase_gate.py", args[0]),
        env=_gil_off(),
    )


def _conformance(args: list[str], repo: str) -> Step:
    """`conformance server <url>`: the pinned MCP conformance suite, run in devtools against a loopback server."""
    match = _MCP_URL.fullmatch(args[1]) if len(args) == 2 and args[0] == "server" else None
    if match is None or not 1024 <= int(match.group("port")) <= 65535:
        raise ActionError(400, "conformance expects: server http://127.0.0.1:<port>/mcp")
    return Step(
        argv=(
            "podman",
            "exec",
            "--workdir",
            repo,
            _DEVTOOLS,
            "npx",
            "--yes",
            _CONFORMANCE,
            "server",
            "--url",
            args[1],
            "--requirements",
            _MCP_REVISION,
        )
    )


def _devtools(args: list[str], repo: str) -> Step:
    """`devtools <extractor> <path>`: one code-graph extractor in devtools, cwd the worktree, path inside it."""
    if len(args) != 2 or args[0] not in _EXTRACTORS:
        raise ActionError(400, f"devtools expects one of {sorted(_EXTRACTORS)} and a path")
    path = args[1]
    if _REPO_PATH.fullmatch(path) is None or ".." in path.split("/"):
        raise ActionError(400, "devtools path must be relative to the worktree")
    if args[0] == "rust-metadata":
        path = f"{path.rstrip('/')}/Cargo.toml"
    return Step(argv=("podman", "exec", "--workdir", repo, _DEVTOOLS, *_EXTRACTORS[args[0]], path))
