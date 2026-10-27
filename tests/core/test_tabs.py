"""APP_API §4.3 through the real IPC connection, plus the host worktree adapter."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import msgspec
import pytest

from aconsole.contracts.app import (
    CloseTabParams,
    CloseTabResult,
    ExitedParams,
    FocusParams,
    OpenTabParams,
    RenameParams,
    ReorderParams,
    RestartParams,
    StartedParams,
    TabIdParams,
    TabView,
    TerminalLaunchSpec,
)
from aconsole.contracts.common import AgentKind, ClientRole, NodeId, ProjectId, TabId, TabStatus, WorkspaceMode
from aconsole.contracts.events import TabAdded, TabRemoved, TabUpdated, UiEvent
from aconsole.contracts.ipc import InitializeParams, ProtocolVersion, Request, ResponseErr, ResponseOk
from aconsole.core.application.services.tabs import ProjectSeed, TabRequestError, TabService
from aconsole.core.domain.clock import FrozenClock
from aconsole.core.infrastructure.ipc.framing import decode_envelope, encode_line
from aconsole.core.infrastructure.ipc.ring import EventRing
from aconsole.core.infrastructure.ipc.session import Connection
from aconsole.core.infrastructure.tabs import HostBins, HostBox, HostTabs, default_tabs

_WHEN = datetime(2026, 10, 27, tzinfo=UTC)
_ENCODER = msgspec.json.Encoder()
_ROOT = Path(__file__).resolve().parents[2]
_PODMAN = _ROOT / "tests" / "fixtures" / "bin" / "podman"
_DISTROBOX = _ROOT / "tests" / "fixtures" / "bin" / "distrobox"
_GIT = Path("/usr/bin/git")
_SHELL = Path("/bin/sh")
_STOP = 3.0
_PID = 424242
_ORPHAN = 424243
_DEAD_PID = 1 << 30
_HEX_ID = 16
_HEX_PROOF = 64


class _Ports:
    """Recording stand-in. It never signals a process or sleeps."""

    def __init__(self) -> None:
        self.ids: list[str] = ["tab-a", "tab-b", "tab-c"]
        self.proofs: list[str] = ["ing-a", "side-a", "side-b", "ing-b", "side-c"]
        self.pauses: list[float] = []
        self.stopped: list[int] = []
        self.created: list[tuple[str, str, str, str]] = []
        self.released: list[str] = []
        self.removed: list[tuple[str, str, bool]] = []
        self.ensured: list[str] = []
        self.state: str = "clean"
        self.fail_ensure: bool = False
        self.create_error: TabRequestError | None = None
        self.orphan_pids: tuple[int, ...] = ()
        self.orphan_error: TabRequestError | None = None
        # The listener of every service built by `_service`. Tests read tab state from these events.
        self.events: list[UiEvent] = []

    def new_id(self) -> str:
        return self.ids.pop(0)

    def new_secret(self) -> str:
        return self.proofs.pop(0)

    def pause(self, seconds: float) -> None:
        self.pauses.append(seconds)

    def stop_process(self, pid: int) -> None:
        self.stopped.append(pid)

    def create_worktree(self, repo: str, tab_id: str, slug: str, base: str) -> tuple[str, str]:
        if self.create_error is not None:
            raise self.create_error
        self.created.append((repo, tab_id, slug, base))
        return f"/wt/{tab_id}", f"ac/{slug}-{tab_id}"

    def worktree_state(self, repo: str, dest: str) -> str:
        _ = (repo, dest)
        return self.state

    def release_worktree(self, dest: str) -> None:
        self.released.append(dest)

    def remove_worktree(self, repo: str, dest: str, *, discard_ignored: bool) -> bool:
        self.removed.append((repo, dest, discard_ignored))
        return True

    def ensure_container(self, name: str) -> None:
        if self.fail_ensure:
            failure = TabRequestError("CONTAINER_START_FAILED")
            raise failure
        self.ensured.append(name)

    def exec_argv(self, name: str, command: tuple[str, ...], env: dict[str, str], workdir: str) -> tuple[str, ...]:
        return ("/usr/bin/podman", "exec", name, workdir, env["ACONSOLE_TAB"], *command)

    def orphans(self, name: str, tab_id: str) -> tuple[int, ...]:
        _ = (name, tab_id)
        if self.orphan_error is not None:
            raise self.orphan_error
        return self.orphan_pids

    def container_path(self, name: str) -> str:
        _ = name
        return "/usr/bin"

    def base_env(self) -> dict[str, str]:
        return {"HOME": "/home/aconsole"}

    def python_bin(self) -> str:
        return "/usr/bin/python3"

    def socket_path(self) -> str:
        return "/run/aconsole.sock"

    def ingest_port(self) -> str:
        return "8765"


def test_tabs_open_does_not_start_and_launch_rotates_the_proof() -> None:
    service, ports = _service(_seed())
    connection = _ready(service)
    opened = _view(_call(connection, 2, "tab/open", _open()))
    assert opened.status is TabStatus.IDLE
    assert opened.started is False
    assert opened.worktree_path == "/wt/tab-a"
    assert opened.branch == "ac/claude-tab-a"
    assert ports.ensured == []
    assert ports.pauses == []
    first = _spec(_call(connection, 3, "tab/launchSpec", _tab("tab-a")))
    second = _spec(_call(connection, 4, "tab/launchSpec", _tab("tab-a")))
    assert ports.ensured == ["agentbox", "agentbox"]
    assert first.argv[:5] == ("/usr/bin/python3", "-I", "-m", "aconsole.sidecar", "--")
    assert first.argv[5:8] == ("/usr/bin/podman", "exec", "agentbox")
    assert first.env["HOME"] == "/home/aconsole"
    assert _value(first, "ACONSOLE_TAB") == "tab-a"
    assert _value(first, "ACONSOLE_TAB_TOKEN") == "ing-a"
    assert _value(first, "ACONSOLE_SIDECAR_TOKEN") == "side-a"
    assert _value(first, "ACONSOLE_SOCKET") == "/run/aconsole.sock"
    assert _value(first, "ACONSOLE_PORT") == "8765"
    assert _value(second, "ACONSOLE_TAB_TOKEN") == "ing-a"
    assert _value(second, "ACONSOLE_SIDECAR_TOKEN") == "side-b"
    assert first.scrollback_lines == 10_000
    assert first.cwd == "/wt/tab-a"
    assert first.title == "chat"


def test_tabs_started_exited_restart_and_fork_share_the_tree() -> None:
    service, ports = _service(_seed())
    connection = _ready(service)
    opened = _view(_call(connection, 2, "tab/open", _open(resume="sess", node="node-1")))
    assert opened.agent_session_id == "sess"
    assert opened.plan_node == NodeId("node-1")
    assert opened.status_since == _WHEN
    started = _call(connection, 3, "tab/started", StartedParams(tab_id=TabId("tab-a"), pid=_PID))
    assert isinstance(started, ResponseOk)
    assert bytes(started.result) == b"{}"
    assert _latest(ports, "tab-a").status is TabStatus.WORKING
    assert _latest(ports, "tab-a").started is True
    exited = _call(connection, 4, "tab/exited", ExitedParams(tab_id=TabId("tab-a"), wait_status=9))
    assert isinstance(exited, ResponseOk)
    assert _latest(ports, "tab-a").status is TabStatus.EXITED
    assert _latest(ports, "tab-a").status_detail == "9"
    assert _latest(ports, "tab-a").started is False
    kept = _view(_call(connection, 5, "tab/restart", RestartParams(tab_id=TabId("tab-a"), resume=True)))
    assert kept.agent_session_id == "sess"
    assert kept.status is TabStatus.IDLE
    assert kept.started is False
    cleared = _view(_call(connection, 6, "tab/restart", RestartParams(tab_id=TabId("tab-a"), resume=False)))
    assert cleared.agent_session_id is None
    forked = _view(_call(connection, 7, "tab/fork", _tab("tab-a")))
    assert forked.id == TabId("tab-b")
    assert forked.worktree_path == opened.worktree_path
    assert forked.title == "fork chat"
    assert ports.created == [("/repo", "tab-a", "claude", "HEAD")]
    ports.state = "dirty"
    closed = _closed(_call(connection, 8, "tab/close", CloseTabParams(tab_id=TabId("tab-b"))))
    assert closed.closed is True
    assert closed.needs_decision is False
    assert ports.removed == []
    assert not _gone(ports, "tab-a")


def test_tabs_close_dirty_asks_then_keep_or_refuse_remove() -> None:
    service, ports = _service(_seed())
    ports.state = "dirty"
    ports.orphan_pids = (_ORPHAN,)
    connection = _ready(service)
    _ = _view(_call(connection, 2, "tab/open", _open()))
    _ = _call(connection, 3, "tab/started", StartedParams(tab_id=TabId("tab-a"), pid=_PID))
    asked = _closed(_call(connection, 4, "tab/close", CloseTabParams(tab_id=TabId("tab-a"))))
    assert asked.closed is False
    assert asked.needs_decision is True
    assert asked.dirty_summary == "dirty"
    assert ports.pauses == []
    assert ports.stopped == []
    assert not _gone(ports, "tab-a")
    refused = _call(connection, 5, "tab/close", CloseTabParams(tab_id=TabId("tab-a"), decision="remove"))
    assert isinstance(refused, ResponseErr)
    assert refused.error.message == "WORKTREE_DIRTY"
    assert not _gone(ports, "tab-a")
    ports.state = "ahead"
    ahead = _closed(_call(connection, 6, "tab/close", CloseTabParams(tab_id=TabId("tab-a"))))
    assert ahead.dirty_summary == "ahead"
    kept = _closed(_call(connection, 7, "tab/close", CloseTabParams(tab_id=TabId("tab-a"), decision="keep")))
    assert kept.closed is True
    assert ports.pauses == [_STOP]
    assert ports.stopped == [_PID, _ORPHAN]
    assert ports.released == ["/wt/tab-a"]
    assert ports.removed == []
    assert _gone(ports, "tab-a")


def test_tabs_clean_close_removes_and_skips_the_pause_without_a_pid() -> None:
    service, ports = _service(_seed())
    connection = _ready(service)
    _ = _view(_call(connection, 2, "tab/open", _open()))
    _ = _view(_call(connection, 3, "tab/open", _open(title="second")))
    assert _latest(ports, "tab-b").order == 1
    kept = _closed(_call(connection, 4, "tab/close", CloseTabParams(tab_id=TabId("tab-a"), decision="keep")))
    assert kept.closed is True
    assert ports.released == ["/wt/tab-a"]
    assert ports.pauses == []
    removed = _closed(_call(connection, 5, "tab/close", CloseTabParams(tab_id=TabId("tab-b"))))
    assert removed.closed is True
    assert removed.dirty_summary is None
    assert ports.removed == [("/repo", "/wt/tab-b", False)]
    missing = _call(connection, 6, "tab/close", CloseTabParams(tab_id=TabId("tab-b")))
    assert isinstance(missing, ResponseErr)
    assert missing.error.message == "TAB_NOT_FOUND"


def test_tabs_ignored_files_ask_then_remove_with_discard() -> None:
    service, ports = _service(_seed())
    ports.state = "ignored"
    connection = _ready(service)
    _ = _view(_call(connection, 2, "tab/open", _open()))
    asked = _closed(_call(connection, 3, "tab/close", CloseTabParams(tab_id=TabId("tab-a"))))
    assert asked.closed is False
    assert asked.needs_decision is True
    assert asked.dirty_summary == "ignored"
    assert ports.removed == []
    assert _present(connection, 90, "tab-a")
    removed = _closed(_call(connection, 4, "tab/close", CloseTabParams(tab_id=TabId("tab-a"), decision="remove")))
    assert removed.closed is True
    assert removed.dirty_summary is None
    assert ports.removed == [("/repo", "/wt/tab-a", True)]
    assert not _present(connection, 91, "tab-a")


def test_tabs_remove_is_refused_for_dirty_and_ahead_trees() -> None:
    service, ports = _service(_seed())
    connection = _ready(service)
    _ = _view(_call(connection, 2, "tab/open", _open()))
    refusals: list[str] = []
    for request_id, state in ((3, "dirty"), (4, "ahead")):
        ports.state = state
        refused = _call(connection, request_id, "tab/close", CloseTabParams(tab_id=TabId("tab-a"), decision="remove"))
        assert isinstance(refused, ResponseErr)
        refusals.append(refused.error.message)
    assert refusals == ["WORKTREE_DIRTY", "WORKTREE_DIRTY"]
    assert ports.removed == []
    assert _present(connection, 90, "tab-a")


def test_tabs_failed_orphan_scan_keeps_the_tree_even_when_asked_to_remove() -> None:
    service, ports = _service(_seed())
    connection = _ready(service)
    _ = _view(_call(connection, 2, "tab/open", _open()))
    _ = _call(connection, 3, "tab/started", StartedParams(tab_id=TabId("tab-a"), pid=_PID))
    ports.orphan_error = TabRequestError("CONTAINER_START_FAILED")
    closed = _closed(_call(connection, 4, "tab/close", CloseTabParams(tab_id=TabId("tab-a"))))
    assert closed.closed is True
    assert ports.stopped == [_PID]
    assert ports.released == ["/wt/tab-a"]
    assert ports.removed == []
    assert not _present(connection, 91, "tab-a")
    _ = _view(_call(connection, 5, "tab/open", _open()))
    ports.state = "ignored"
    kept = _closed(_call(connection, 6, "tab/close", CloseTabParams(tab_id=TabId("tab-b"), decision="remove")))
    assert kept.closed is True
    assert ports.released == ["/wt/tab-a", "/wt/tab-b"]
    assert ports.removed == []


def test_tabs_rename_reorder_focus_and_main_checkout() -> None:
    service, ports = _service(_seed())
    connection = _ready(service)
    main = _view(
        _call(connection, 2, "tab/open", _open(workspace=WorkspaceMode.MAIN_CHECKOUT, title=None, base="main")),
    )
    assert main.worktree_path == "/repo"
    assert main.branch == "main"
    assert main.title == "claude-tab-a"
    assert ports.created == []
    renamed = _call(connection, 3, "tab/rename", RenameParams(tab_id=TabId("tab-a"), title="renamed"))
    assert isinstance(renamed, ResponseOk)
    moved = _call(connection, 4, "tab/reorder", ReorderParams(tab_id=TabId("tab-a"), order=4))
    assert isinstance(moved, ResponseOk)
    blurred = _call(connection, 5, "tab/focus", FocusParams(tab_id=TabId("tab-a"), window_active=False))
    assert isinstance(blurred, ResponseOk)
    assert _latest(ports, "tab-a").title == "renamed"
    assert _latest(ports, "tab-a").order == 4
    # APP_API §8: attention = (waiting_approval | waiting_input | error) and not (focused tab and active window).
    hooked = service.apply_hook("tab-a", "PermissionRequest")
    assert hooked is not None
    assert hooked.status is TabStatus.WAITING_APPROVAL
    assert hooked.attention is True
    awake = _call(connection, 7, "tab/focus", FocusParams(tab_id=TabId("tab-a"), window_active=True))
    assert isinstance(awake, ResponseOk)
    assert _latest(ports, "tab-a").attention is False
    seen = service.apply_hook("tab-a", "PermissionRequest")
    assert seen is not None
    assert seen.attention is False
    idle = _call(connection, 8, "tab/focus", FocusParams(tab_id=None, window_active=True))
    assert isinstance(idle, ResponseOk)
    assert bytes(idle.result) == b"{}"
    unseen = service.apply_hook("tab-a", "PermissionRequest")
    assert unseen is not None
    assert unseen.attention is True
    # Restart leaves the tab idle, and idle never needs the user.
    cleared = _view(_call(connection, 9, "tab/restart", RestartParams(tab_id=TabId("tab-a"), resume=True)))
    assert cleared.status is TabStatus.IDLE
    assert cleared.attention is False
    long_title = "x" * 100
    trimmed = _view(_call(connection, 10, "tab/open", _open(title=long_title)))
    assert trimmed.title == "x" * 80
    assert trimmed.order == 5


def test_tabs_existing_worktree_and_open_errors() -> None:
    logged_out_book, logged_out_ports = _service(
        ProjectSeed(project_id="proj", root="/repo", container="agentbox", logged_in=False),
    )
    connection = _ready(logged_out_book)
    warning = _view(_call(connection, 2, "tab/open", _open()))
    assert warning.status_detail == "AGENT_NOT_LOGGED_IN"
    assert warning.status is TabStatus.IDLE
    missing_path = _call(
        connection,
        3,
        "tab/open",
        _open(workspace=WorkspaceMode.EXISTING_WORKTREE),
    )
    assert isinstance(missing_path, ResponseErr)
    assert missing_path.error.message == "WORKTREE_CREATE_FAILED"
    existing = _view(
        _call(connection, 4, "tab/open", _open(workspace=WorkspaceMode.EXISTING_WORKTREE, existing="/wt/given")),
    )
    assert existing.worktree_path == "/wt/given"
    assert logged_out_ports.created == [("/repo", "tab-a", "claude", "HEAD")]
    service, ports = _service(_seed(), ProjectSeed(project_id="down", root="/repo", container="box", available=False))
    connection = _ready(service)
    unknown = _call(connection, 2, "tab/open", _open(project="missing"))
    assert isinstance(unknown, ResponseErr)
    assert unknown.error.message == "PROJECT_NOT_FOUND"
    unavailable = _call(connection, 3, "tab/open", _open(project="down"))
    assert isinstance(unavailable, ResponseErr)
    assert unavailable.error.message == "AGENT_UNAVAILABLE"
    absent = _call(connection, 4, "tab/open", _open(resume="no-such-session"))
    assert isinstance(absent, ResponseErr)
    assert absent.error.message == "SESSION_NOT_FOUND"
    _ = _view(_call(connection, 5, "tab/open", _open()))
    ports.fail_ensure = True
    failed = _call(connection, 6, "tab/launchSpec", _tab("tab-a"))
    assert isinstance(failed, ResponseErr)
    assert failed.error.message == "CONTAINER_START_FAILED"
    lost = _call(connection, 7, "tab/launchSpec", _tab("missing"))
    assert isinstance(lost, ResponseErr)
    assert lost.error.message == "TAB_NOT_FOUND"


def test_tabs_checked_out_branch_and_invalid_rename() -> None:
    service, ports = _service(_seed())
    ports.create_error = TabRequestError("BRANCH_ALREADY_CHECKED_OUT")
    connection = _ready(service)
    checked = _call(connection, 2, "tab/open", _open(name="feature"))
    assert isinstance(checked, ResponseErr)
    assert checked.error.message == "BRANCH_ALREADY_CHECKED_OUT"
    assert ports.created == []
    ports.create_error = None
    _ = _view(_call(connection, 3, "tab/open", _open(name="feature")))
    assert ports.created == [("/repo", "tab-b", "feature", "HEAD")]
    blank = _call(connection, 4, "tab/rename", b'{"tab_id":"tab-b","title":""}')
    assert isinstance(blank, ResponseErr)
    assert blank.error.message == "INVALID_PARAMS"
    none = _call(connection, 5, "tab/focus", FocusParams(tab_id=TabId("missing"), window_active=False))
    assert isinstance(none, ResponseErr)
    assert none.error.message == "TAB_NOT_FOUND"
    with pytest.raises(ValueError, match="negative"):
        _ = TabService(FrozenClock(_WHEN), _Ports(), stop_s=-1)
    with pytest.raises(TabRequestError, match="METHOD_NOT_FOUND"):
        _ = service.dispatch("tab/nope", b"{}")
    assert not _gone(ports, "tab-b")


def test_tabs_fork_can_create_a_new_worktree() -> None:
    service, ports = _service(_seed(), fork_new=True)
    connection = _ready(service)
    _ = _view(_call(connection, 2, "tab/open", _open()))
    forked = _view(_call(connection, 3, "tab/fork", _tab("tab-a")))
    assert forked.worktree_path == "/wt/tab-b"
    assert forked.branch == "ac/claude-fork-tab-b"
    assert ports.created[-1] == ("/repo", "tab-b", "claude-fork", "HEAD")
    closed = _closed(_call(connection, 4, "tab/close", CloseTabParams(tab_id=TabId("tab-b"))))
    assert closed.closed is True
    assert ports.removed == [("/repo", "/wt/tab-b", False)]


def test_tabs_missing_service_reports_method_not_found() -> None:
    connection = Connection(EventRing())
    ready = connection.receive(_init())
    assert ready
    missing = _call(connection, 2, "tab/open", _open())
    assert isinstance(missing, ResponseErr)
    assert missing.error.message == "METHOD_NOT_FOUND"
    unwired = _call(connection, 3, "tab/open", b"{}")
    assert isinstance(unwired, ResponseErr)
    assert unwired.error.message == "METHOD_NOT_FOUND"
    made = default_tabs()
    assert isinstance(made, TabService)


def test_host_tabs_open_launch_and_close(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, repo = _host(tmp_path, monkeypatch)
    connection = _ready(service)
    opened = _view(_call(connection, 2, "tab/open", _open(title="host")))
    assert opened.worktree_path == str(tmp_path / "worktrees" / repo.name / opened.id)
    assert Path(opened.worktree_path).is_dir()
    first = _spec(_call(connection, 3, "tab/launchSpec", _tab(str(opened.id))))
    second = _spec(_call(connection, 4, "tab/launchSpec", _tab(str(opened.id))))
    assert first.argv[:5] == (sys.executable, "-I", "-m", "aconsole.sidecar", "--")
    assert str(_PODMAN) in first.argv
    assert "exec" in first.argv
    assert "agentbox" in first.argv
    assert _value(first, "ACONSOLE_TAB") == str(opened.id)
    assert _value(first, "ACONSOLE_PORT") == "9"
    assert len(_value(first, "ACONSOLE_SIDECAR_TOKEN")) == _HEX_PROOF
    assert _value(first, "ACONSOLE_SIDECAR_TOKEN") != _value(second, "ACONSOLE_SIDECAR_TOKEN")
    assert _value(first, "ACONSOLE_TAB_TOKEN") == _value(second, "ACONSOLE_TAB_TOKEN")
    _ = (Path(opened.worktree_path) / "extra.txt").write_text("x\n", encoding="utf-8")
    asked = _closed(_call(connection, 5, "tab/close", CloseTabParams(tab_id=opened.id)))
    assert asked.needs_decision is True
    assert asked.dirty_summary == "dirty"
    assert Path(opened.worktree_path).is_dir()
    other = _view(_call(connection, 6, "tab/open", _open(title="clean")))
    closed = _closed(_call(connection, 7, "tab/close", CloseTabParams(tab_id=other.id)))
    assert closed.closed is True
    assert not Path(other.worktree_path).exists()


def test_host_tabs_container_paths_and_signals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "podman.log"
    monkeypatch.setenv("PODMAN_FAKE_LOG", str(log))
    monkeypatch.setenv("PODMAN_FAKE_RUNNING", "false")
    monkeypatch.setenv("PODMAN_FAKE_ORPHANS", "424242")
    monkeypatch.setenv("DISTROBOX_FAKE_PATH", "/opt/bin")
    host = HostTabs(bins=HostBins(podman=_PODMAN), box=HostBox(ini=tmp_path / "box.ini", distrobox=_DISTROBOX))
    _ = (tmp_path / "box.ini").write_text("[agentbox]\n", encoding="utf-8")
    host.ensure_container("agentbox")
    assert "start agentbox" in log.read_text(encoding="utf-8")
    assert host.container_path("agentbox") == "/opt/bin"
    assert host.orphans("agentbox", "tab1") == (424242,)
    monkeypatch.setenv("PODMAN_FAKE_EXEC_CODE", "1")
    with pytest.raises(TabRequestError, match="CONTAINER_START_FAILED"):
        _ = host.orphans("agentbox", "tab1")
    with pytest.raises(TabRequestError, match="CONTAINER_START_FAILED"):
        _ = host.orphans("bad name", "tab1")
    with pytest.raises(TabRequestError, match="CONTAINER_START_FAILED"):
        host.ensure_container("broken")
    with pytest.raises(TabRequestError, match="CONTAINER_START_FAILED"):
        host.ensure_container("absent")
    with pytest.raises(TabRequestError, match="CONTAINER_START_FAILED"):
        host.ensure_container("bad name")
    argv = host.exec_argv("agentbox", ("claude",), {"ACONSOLE_TAB": "tab1", "PATH": "/opt/bin"}, "/work")
    assert argv[0] == str(_PODMAN)
    assert "exec" in argv
    with pytest.raises(TabRequestError, match="CONTAINER_START_FAILED"):
        _ = host.exec_argv("bad name", ("claude",), {}, "/work")
    missing = HostTabs(bins=HostBins(podman=_PODMAN), box=HostBox(ini=tmp_path / "no.ini", distrobox=_DISTROBOX))
    with pytest.raises(TabRequestError, match="CONTAINER_START_FAILED"):
        _ = missing.container_path("agentbox")
    plain = HostTabs(bins=HostBins(podman=_PODMAN), socket_path="/sock", ingest_port="4")
    assert plain.container_path("agentbox") == "/usr/bin"
    assert plain.socket_path() == "/sock"
    assert plain.ingest_port() == "4"
    assert plain.python_bin() == sys.executable
    assert "PATH" in plain.base_env()
    assert plain.pause(0) is None
    assert plain.stop_process(_DEAD_PID) is None
    assert len(plain.new_id()) == _HEX_ID
    assert len(plain.new_secret()) == _HEX_PROOF


def test_host_tabs_worktree_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PODMAN_FAKE_RUNNING", "true")
    repo = tmp_path / "repo"
    _init_repo(repo)
    host = HostTabs(
        bins=HostBins(git=_GIT, podman=_PODMAN, shell=_SHELL),
        worktrees_dir=tmp_path / "worktrees",
    )
    _ = _git(repo, "branch", "ac/claude-taken")
    with pytest.raises(TabRequestError, match="BRANCH_ALREADY_CHECKED_OUT"):
        _ = host.create_worktree(str(repo), "taken", "claude", "HEAD")
    (tmp_path / "worktrees" / repo.name / "tab1").mkdir(parents=True)
    with pytest.raises(TabRequestError, match="BRANCH_ALREADY_CHECKED_OUT"):
        _ = host.create_worktree(str(repo), "tab1", "claude", "HEAD")
    with pytest.raises(TabRequestError, match="WORKTREE_CREATE_FAILED"):
        _ = host.create_worktree(str(repo), "bad id", "claude", "HEAD")
    dest, branch = host.create_worktree(str(repo), "fresh", "claude", "HEAD")
    assert branch == "ac/claude-fresh"
    assert host.worktree_state(str(repo), dest) == "clean"
    _ = (Path(dest) / "extra.txt").write_text("x\n", encoding="utf-8")
    assert host.worktree_state(str(repo), dest) == "dirty"
    assert host.remove_worktree(str(repo), dest, discard_ignored=False) is False
    assert host.remove_worktree(str(repo), dest, discard_ignored=True) is False
    assert Path(dest).is_dir()
    with pytest.raises(TabRequestError, match="WORKTREE_CREATE_FAILED"):
        _ = host.worktree_state(str(repo), str(tmp_path / "missing"))
    orphan = tmp_path / "orphan"
    orphan.mkdir()
    with pytest.raises(TabRequestError, match="WORKTREE_CREATE_FAILED"):
        _ = host.remove_worktree(str(repo), str(orphan), discard_ignored=False)
    host.release_worktree(dest)


def _present(connection: Connection, request_id: int, tab_id: str) -> bool:
    """A tab exists while `tab/focus` on it succeeds. Focusing it with the window active changes nothing else."""
    found = _call(connection, request_id, "tab/focus", FocusParams(tab_id=TabId(tab_id), window_active=True))
    return isinstance(found, ResponseOk)


def _service(*seeds: ProjectSeed, fork_new: bool = False) -> tuple[TabService, _Ports]:
    ports = _Ports()
    service = TabService(FrozenClock(_WHEN), ports, fork_new_worktree=fork_new, listener=ports.events.append)
    for seed in seeds:
        service.add_project(seed)
    return service, ports


def _latest(ports: _Ports, tab_id: str) -> TabView:
    """The newest view of a tab that the listener emitted. Tests read tab state through events, not the book."""
    for event in reversed(ports.events):
        if isinstance(event, (TabAdded, TabUpdated)) and str(event.tab.id) == tab_id:
            return event.tab
    pytest.fail(f"no tab event for {tab_id}")


def _gone(ports: _Ports, tab_id: str) -> bool:
    return any(isinstance(event, TabRemoved) and str(event.tab_id) == tab_id for event in ports.events)


def _seed() -> ProjectSeed:
    return ProjectSeed(project_id="proj", root="/repo", container="agentbox", sessions=frozenset({"sess"}))


def _open(
    *,
    project: str = "proj",
    workspace: WorkspaceMode = WorkspaceMode.NEW_WORKTREE,
    title: str | None = "chat",
    resume: str | None = None,
    existing: str | None = None,
    name: str | None = None,
    base: str | None = None,
    node: str | None = None,
) -> OpenTabParams:
    return OpenTabParams(
        project_id=ProjectId(project),
        agent=AgentKind.CLAUDE,
        workspace=workspace,
        base_branch=base,
        worktree_name=name,
        existing_worktree=existing,
        plan_node=None if node is None else NodeId(node),
        resume_session=resume,
        title=title,
    )


def _tab(tab_id: str) -> TabIdParams:
    return TabIdParams(tab_id=TabId(tab_id))


def _line(value: msgspec.Struct) -> bytes:
    return encode_line(_ENCODER, value)


def _init() -> bytes:
    params = InitializeParams(
        protocol=(ProtocolVersion(major=1, minor=0), ProtocolVersion(major=1, minor=0)),
        role=ClientRole.UI,
        client_name="test",
        client_version="0.0.0",
        pid=1,
        capabilities=frozenset({"events.v1"}),
    )
    return _line(Request(id=1, method="initialize", params=msgspec.Raw(_ENCODER.encode(params))))


def _ready(service: TabService) -> Connection:
    connection = Connection(EventRing(), tabs=service)
    assert connection.receive(_init())
    return connection


def _call(
    connection: Connection,
    request_id: int,
    method: str,
    params: msgspec.Struct | bytes,
) -> ResponseOk | ResponseErr:
    payload = params if isinstance(params, bytes) else _ENCODER.encode(params)
    frames = connection.receive(_line(Request(id=request_id, method=method, params=msgspec.Raw(payload))))
    assert len(frames) == 1
    found = decode_envelope(frames[0])
    assert isinstance(found, (ResponseOk, ResponseErr))
    return found


def _view(found: ResponseOk | ResponseErr) -> TabView:
    assert isinstance(found, ResponseOk)
    return msgspec.json.decode(found.result, type=TabView)


def _spec(found: ResponseOk | ResponseErr) -> TerminalLaunchSpec:
    assert isinstance(found, ResponseOk)
    return msgspec.json.decode(found.result, type=TerminalLaunchSpec)


def _value(spec: TerminalLaunchSpec, key: str) -> str:
    return spec.env[key]


def _closed(found: ResponseOk | ResponseErr) -> CloseTabResult:
    assert isinstance(found, ResponseOk)
    return msgspec.json.decode(found.result, type=CloseTabResult)


def _host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TabService, Path]:
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.setenv("PODMAN_FAKE_RUNNING", "true")
    monkeypatch.delenv("PODMAN_FAKE_ORPHANS", raising=False)
    host = HostTabs(
        bins=HostBins(git=_GIT, podman=_PODMAN, shell=_SHELL),
        socket_path=str(tmp_path / "core.sock"),
        ingest_port="9",
        worktrees_dir=tmp_path / "worktrees",
    )
    service = TabService(FrozenClock(_WHEN), host)
    service.add_project(ProjectSeed(project_id="proj", root=str(repo), container="agentbox"))
    return service, repo


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    _ = _git(repo, "init", "-b", "main")
    _ = (repo / "README").write_text("hi\n", encoding="utf-8")
    _ = _git(repo, "add", "README")
    _ = _git(repo, "-c", "user.email=aconsole@localhost", "-c", "user.name=aconsole", "commit", "-m", "init")


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        [str(_GIT), "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout
