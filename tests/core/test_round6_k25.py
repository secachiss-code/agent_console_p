"""Round 6 K2.5: tab wiring. Listener events, hook mapping, launch environment, sidecar claims, session gating,
the ingest queue and tokens, and one daemon run in-process over the real socket and HTTP."""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, final

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
    SubscribeParams,
    TabIdParams,
    TabView,
    TerminalLaunchSpec,
)
from aconsole.contracts.common import AgentKind, ClientRole, ProjectId, TabId, TabStatus, WorkspaceMode
from aconsole.contracts.events import Notify, TabAdded, TabRemoved, TabUpdated, UiEvent
from aconsole.contracts.ipc import (
    Envelope,
    EventEnvelope,
    InitializeParams,
    Notification,
    ProtocolVersion,
    Request,
    ResponseErr,
    ResponseOk,
)
from aconsole.core.application.services.tabs import ProjectSeed, TabRequestError, TabService
from aconsole.core.bootstrap import Daemon
from aconsole.core.domain.clock import FrozenClock
from aconsole.core.infrastructure.http.auth import Decision, TokenBook
from aconsole.core.infrastructure.http.consumer import hook_event
from aconsole.core.infrastructure.http.routes import IngestItem, IngestQueue
from aconsole.core.infrastructure.ipc.framing import decode_envelope, encode_line
from aconsole.core.infrastructure.ipc.ring import EventRing
from aconsole.core.infrastructure.ipc.session import Connection
from tests.core.conftest import FakeTabPorts, use_fake_tabs, wait_for_socket

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from pathlib import Path

_WHEN = datetime(2026, 10, 27, tzinfo=UTC)
_ENCODER = msgspec.json.Encoder()
_PID = 424242
_ORPHAN = 424243
_STOP_S = 3.0
_TIMEOUT_S = 5.0
_FLOW_S = 30.0
# A 50 ms sleep on the loop may run late by this much at most while a tab port call blocks a worker.
_LAG_S = 0.2
_PROJECT = ProjectId("proj")
_PERMISSION = (
    b'{"session_id":"s-1","hook_event_name":"PermissionRequest","tool_name":"Bash","tool_input":{"command":"ls"}}'
)
_WAITING = frozenset({TabStatus.WAITING_APPROVAL, TabStatus.WAITING_INPUT, TabStatus.ERROR})
_MAPPING: tuple[tuple[str, TabStatus], ...] = (
    ("PermissionRequest", TabStatus.WAITING_APPROVAL),
    ("Notification", TabStatus.WAITING_INPUT),
    ("StopFailure", TabStatus.ERROR),
    ("Stop", TabStatus.IDLE),
    ("SessionStart", TabStatus.IDLE),
    ("SessionEnd", TabStatus.IDLE),
    ("agent-turn-complete", TabStatus.IDLE),
    ("UserPromptSubmit", TabStatus.WORKING),
    ("PreToolUse", TabStatus.WORKING),
    ("PostToolUse", TabStatus.WORKING),
    ("SubagentStop", TabStatus.WORKING),
)


class _Problem(msgspec.Struct, frozen=True, kw_only=True):
    status: int
    reason: str


def _service(*, fork_new: bool = False) -> tuple[TabService, FakeTabPorts, list[UiEvent]]:
    """A tab book on fake ports. Its listener appends to the returned list."""
    ports = FakeTabPorts()
    events: list[UiEvent] = []
    service = TabService(FrozenClock(_WHEN), ports, fork_new_worktree=fork_new, listener=events.append)
    service.add_project(ProjectSeed(project_id="proj", root="/repo", container="agentbox"))
    return service, ports, events


def _open(*, workspace: WorkspaceMode = WorkspaceMode.NEW_WORKTREE, title: str | None = "chat") -> OpenTabParams:
    return OpenTabParams(project_id=_PROJECT, agent=AgentKind.CLAUDE, workspace=workspace, title=title)


def _last_update(events: list[UiEvent]) -> TabUpdated:
    last = events[-1]
    assert isinstance(last, TabUpdated)
    return last


def _launch(service: TabService, tab: TabId) -> TerminalLaunchSpec:
    return service.launch_spec(TabIdParams(tab_id=tab))


def _secret(spec: TerminalLaunchSpec) -> str:
    return spec.env["ACONSOLE_SIDECAR_TOKEN"]


def _notice(name: str) -> Notify:
    return Notify(tab_id=None, title=name, body=name, priority="normal", episode_id=name)


def _line(value: msgspec.Struct) -> bytes:
    return encode_line(_ENCODER, value)


def _initialize(role: ClientRole, *, tab_id: TabId | None = None, proof: str | None = None) -> InitializeParams:
    return InitializeParams(
        protocol=(ProtocolVersion(major=1, minor=0), ProtocolVersion(major=1, minor=0)),
        role=role,
        client_name="test",
        client_version="0.0.0",
        pid=1,
        capabilities=frozenset({"events.v1"}),
        tab_id=tab_id,
        sidecar_token=proof,
    )


def _init_frame(role: ClientRole, *, tab_id: TabId | None = None, proof: str | None = None) -> bytes:
    params = _initialize(role, tab_id=tab_id, proof=proof)
    return _line(Request(id=1, method="initialize", params=msgspec.Raw(_ENCODER.encode(params))))


def _request(request_id: int, method: str, params: msgspec.Struct | bytes) -> bytes:
    payload = params if isinstance(params, bytes) else _ENCODER.encode(params)
    return _line(Request(id=request_id, method=method, params=msgspec.Raw(payload)))


def _reply(connection: Connection, frame: bytes) -> Envelope:
    frames = connection.receive(frame)
    assert len(frames) == 1
    return decode_envelope(frames[0])


def _code(envelope: Envelope) -> str:
    assert isinstance(envelope, ResponseErr)
    return envelope.error.message


# --- listener events -------------------------------------------------------------------------------------------


def test_round6_k25_listener_announces_added_tabs() -> None:
    service, ports, events = _service(fork_new=True)
    opened = service.open_tab(_open())
    assert events == [TabAdded(tab=opened)]
    forked = service.fork(TabIdParams(tab_id=opened.id))
    assert events[-1] == TabAdded(tab=forked)
    assert forked.id != opened.id
    assert ports.created[-1] == ("/repo", str(forked.id), "claude-fork", "HEAD")


def test_round6_k25_listener_names_the_changed_fields() -> None:
    service, _ports, events = _service()
    opened = service.open_tab(_open())
    tab = opened.id
    _ = service.started(StartedParams(tab_id=tab, pid=_PID))
    assert _last_update(events).changed == frozenset({"started", "status", "status_since", "attention"})
    _ = service.exited(ExitedParams(tab_id=tab, wait_status=9))
    assert _last_update(events).changed == frozenset(
        {"started", "status", "status_detail", "status_since", "attention"},
    )
    _ = service.rename(RenameParams(tab_id=tab, title="renamed"))
    assert _last_update(events).changed == frozenset({"title"})
    _ = service.reorder(ReorderParams(tab_id=tab, order=3))
    assert _last_update(events).changed == frozenset({"order"})
    _ = service.restart(RestartParams(tab_id=tab, resume=True))
    assert _last_update(events).changed == frozenset(
        {"started", "status", "status_since", "attention", "agent_session_id"},
    )
    _ = service.apply_hook(str(tab), "Notification")
    assert _last_update(events).changed == frozenset({"status", "status_since", "attention"})
    _ = service.apply_hook(str(tab), "Stop")
    assert _last_update(events).changed == frozenset({"status", "status_since", "attention"})
    _ = service.apply_hook(str(tab), "UserPromptSubmit")
    assert _last_update(events).changed == frozenset({"status", "status_since"})


def test_round6_k25_listener_reports_focus_changes_and_close() -> None:
    service, _ports, events = _service()
    opened = service.open_tab(_open())
    tab = opened.id
    _ = service.apply_hook(str(tab), "PermissionRequest")
    assert _last_update(events).tab.attention is True
    before = len(events)
    _ = service.focus(FocusParams(tab_id=tab, window_active=True))
    assert _last_update(events).changed == frozenset({"attention"})
    assert _last_update(events).tab.attention is False
    _ = service.focus(FocusParams(tab_id=tab, window_active=True))
    assert len(events) == before + 1
    _ = service.close_tab(CloseTabParams(tab_id=tab))
    assert events[-1] == TabRemoved(tab_id=tab)


# --- hooks and attention -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("hook", "status"), _MAPPING)
def test_round6_k25_apply_hook_maps_each_event(hook: str, status: TabStatus) -> None:
    service, _ports, events = _service()
    opened = service.open_tab(_open())
    view = service.apply_hook(str(opened.id), hook)
    assert view is not None
    assert view.status is status
    # APP_API §8: the waiting statuses and error need the user; no focus is recorded here.
    assert view.attention == (status in _WAITING)
    assert _last_update(events).tab == view


def test_round6_k25_apply_hook_ignores_unknown_events_and_tabs() -> None:
    service, _ports, events = _service()
    opened = service.open_tab(_open())
    before = len(events)
    assert service.apply_hook(str(opened.id), "NoSuchEvent") is None
    assert service.apply_hook("missing", "Stop") is None
    assert len(events) == before


def test_round6_k25_waiting_tab_needs_attention_unless_focused_in_an_active_window() -> None:
    service, _ports, events = _service()
    opened = service.open_tab(_open())
    tab = opened.id
    _ = service.apply_hook(str(tab), "PermissionRequest")
    assert _last_update(events).tab.attention is True
    _ = service.focus(FocusParams(tab_id=tab, window_active=True))
    assert _last_update(events).tab.attention is False
    _ = service.focus(FocusParams(tab_id=tab, window_active=False))
    assert _last_update(events).tab.attention is True
    _ = service.focus(FocusParams(tab_id=None, window_active=True))
    assert _last_update(events).tab.attention is True
    _ = service.apply_hook(str(tab), "Stop")
    assert _last_update(events).tab.attention is False


# --- launch environment and sidecar claims ---------------------------------------------------------------------


def test_round6_k25_launch_spec_splits_the_environment() -> None:
    service, ports, _events = _service()
    opened = service.open_tab(_open())
    tab = str(opened.id)
    spec = _launch(service, opened.id)
    token = service.ingest_token(tab)
    assert token is not None
    assert ports.ensured == ["agentbox"]
    # Inside the container: the tab, its ingest token, the ingest port, and PATH. Never the sidecar secret.
    name, command, inner, workdir = ports.exec_calls[-1]
    assert (name, command, workdir) == ("agentbox", ("claude",), opened.worktree_path)
    assert inner == {
        "ACONSOLE_TAB": tab,
        "ACONSOLE_TAB_TOKEN": token,
        "ACONSOLE_PORT": "8765",
        "PATH": "/opt/agent/bin:/usr/bin",
    }
    assert spec.argv == (
        "/usr/bin/python3",
        "-I",
        "-m",
        "aconsole.sidecar",
        "--",
        "/usr/bin/podman",
        "exec",
        "agentbox",
        opened.worktree_path,
        "claude",
    )
    assert spec.cwd == opened.worktree_path
    # On the host: the sidecar secret, the socket, and the port the sidecar dials.
    assert spec.env["ACONSOLE_TAB"] == tab
    assert spec.env["ACONSOLE_TAB_TOKEN"] == token
    assert spec.env["ACONSOLE_PORT"] == "8765"
    assert spec.env["ACONSOLE_SOCKET"] == "/run/aconsole.sock"
    assert spec.env["HOME"] == "/home/aconsole"
    assert _secret(spec) != ""
    assert "ACONSOLE_SIDECAR_TOKEN" not in inner


def test_round6_k25_relaunch_rotates_the_sidecar_secret_and_keeps_the_ingest_token() -> None:
    service, _ports, _events = _service()
    opened = service.open_tab(_open())
    tab = str(opened.id)
    first = _launch(service, opened.id)
    second = _launch(service, opened.id)
    assert _secret(first) != _secret(second)
    assert first.env["ACONSOLE_TAB_TOKEN"] == second.env["ACONSOLE_TAB_TOKEN"]
    assert not service.claim_sidecar(tab, _secret(first))
    assert service.claim_sidecar(tab, _secret(second))


def test_round6_k25_sidecar_claim_is_one_shot_and_needs_the_right_token() -> None:
    service, _ports, _events = _service()
    opened = service.open_tab(_open())
    tab = str(opened.id)
    secret = _secret(_launch(service, opened.id))
    assert not service.claim_sidecar(tab, secret + "x")
    assert not service.claim_sidecar("missing", secret)
    assert service.claim_sidecar(tab, secret)
    assert not service.claim_sidecar(tab, secret)


# --- stop, restart, and liveness -----------------------------------------------------------------------------


def test_round6_k25_restart_stops_the_old_process_and_its_orphans() -> None:
    service, ports, events = _service()
    opened = service.open_tab(_open())
    tab = opened.id
    _ = service.started(StartedParams(tab_id=tab, pid=_PID))
    ports.orphan_pids = (_ORPHAN,)
    restarted = service.restart(RestartParams(tab_id=tab, resume=True))
    assert ports.stopped == [_PID, _ORPHAN]
    assert ports.pauses == [_STOP_S]
    assert ports.scans == [("agentbox", str(tab))]
    assert restarted.status is TabStatus.IDLE
    assert restarted.started is False
    # No pid: no stop wait and no signal, but the orphan scan still runs.
    ports.orphan_pids = ()
    _ = service.restart(RestartParams(tab_id=tab, resume=False))
    assert ports.stopped == [_PID, _ORPHAN]
    assert ports.pauses == [_STOP_S]
    assert ports.scans[-1] == ("agentbox", str(tab))
    # A failed scan still stops the known pid, but the restart fails: an orphan may still hold the tree.
    _ = service.started(StartedParams(tab_id=tab, pid=_PID + 1))
    ports.orphan_error = TabRequestError("CONTAINER_START_FAILED")
    with pytest.raises(TabRequestError, match="CONTAINER_START_FAILED"):
        _ = service.restart(RestartParams(tab_id=tab, resume=True))
    assert ports.stopped[-1] == _PID + 1
    assert len(ports.stopped) == 3
    assert service.running_count() == 0
    stopped = _last_update(events)
    assert stopped.changed == frozenset({"started"})
    assert stopped.tab.started is False
    assert stopped.tab.status is not TabStatus.IDLE


def test_round6_k25_running_count_tracks_started_and_exited_tabs() -> None:
    service, _ports, _events = _service()
    first = service.open_tab(_open())
    second = service.open_tab(_open(title="second"))
    assert service.running_count() == 0
    _ = service.started(StartedParams(tab_id=first.id, pid=_PID))
    _ = service.started(StartedParams(tab_id=second.id, pid=_ORPHAN))
    assert service.running_count() == 2
    _ = service.exited(ExitedParams(tab_id=first.id, wait_status=0))
    assert service.running_count() == 1
    _ = service.close_tab(CloseTabParams(tab_id=second.id))
    assert service.running_count() == 0
    assert service.ingest_token("missing") is None


# --- session role gating and the handshake ----------------------------------------------------------------


def _sidecar_session(service: TabService, tab: TabId, proof: str) -> Connection:
    connection = Connection(EventRing(), tabs=service)
    ready = _reply(connection, _init_frame(ClientRole.SIDECAR, tab_id=tab, proof=proof))
    assert isinstance(ready, ResponseOk)
    return connection


def test_round6_k25_sidecar_may_only_call_sidecar_methods() -> None:
    service, _ports, events = _service()
    opened = service.open_tab(_open())
    connection = _sidecar_session(service, opened.id, _secret(_launch(service, opened.id)))
    before = len(events)
    renamed = _reply(connection, _request(2, "tab/rename", RenameParams(tab_id=opened.id, title="nope")))
    assert _code(renamed) == "ROLE_FORBIDDEN"
    subscribed = _reply(connection, _request(3, "events/subscribe", SubscribeParams(after_seq=0)))
    assert _code(subscribed) == "ROLE_FORBIDDEN"
    assert len(events) == before


def test_round6_k25_ui_may_not_call_sidecar_methods() -> None:
    service, _ports, _events = _service()
    connection = Connection(EventRing(), tabs=service)
    ready = _reply(connection, _init_frame(ClientRole.UI))
    assert isinstance(ready, ResponseOk)
    refused = _reply(connection, _request(2, "sidecar/started", b"{}"))
    assert _code(refused) == "ROLE_FORBIDDEN"


def test_round6_k25_sidecar_initialize_needs_a_live_one_shot_token() -> None:
    service, _ports, _events = _service()
    opened = service.open_tab(_open())
    tab = opened.id
    secret = _secret(_launch(service, tab))
    forged = Connection(EventRing(), tabs=service)
    assert _code(_reply(forged, _init_frame(ClientRole.SIDECAR, tab_id=tab, proof=secrets.token_hex(32)))) == (
        "SIDECAR_TOKEN_INVALID"
    )
    assert not forged.ready
    no_tab = Connection(EventRing(), tabs=service)
    assert _code(_reply(no_tab, _init_frame(ClientRole.SIDECAR, proof=secret))) == "SIDECAR_TOKEN_INVALID"
    accepted = Connection(EventRing(), tabs=service)
    assert isinstance(_reply(accepted, _init_frame(ClientRole.SIDECAR, tab_id=tab, proof=secret)), ResponseOk)
    replayed = Connection(EventRing(), tabs=service)
    assert _code(_reply(replayed, _init_frame(ClientRole.SIDECAR, tab_id=tab, proof=secret))) == (
        "SIDECAR_TOKEN_INVALID"
    )


def test_round6_k25_deliver_waits_for_subscribe() -> None:
    connection = Connection(EventRing())
    assert connection.push_event(_notice("before-init"), _WHEN) == ()
    ready = _reply(connection, _init_frame(ClientRole.UI))
    assert isinstance(ready, ResponseOk)
    assert connection.push_event(_notice("before-subscribe"), _WHEN) == ()
    subscribed = connection.receive(_request(2, "events/subscribe", SubscribeParams(after_seq=0)))
    # Both events were numbered before the subscribe, so they arrive as replay after the reply.
    assert len(subscribed) == 3
    assert isinstance(decode_envelope(subscribed[0]), ResponseOk)
    assert len(connection.push_event(_notice("live"), _WHEN)) == 1


# --- ingest queue, hooks, and tokens -----------------------------------------------------------------------


def _item(tab: str, kind: str, body: bytes) -> IngestItem:
    return IngestItem(tab_id=tab, kind=kind, body=body)


def test_round6_k25_ingest_queue_pops_in_order_then_the_latest_overflow_per_tab() -> None:
    queue = IngestQueue(limit=2)
    first_a = _item("a", "claude.hook", b"1")
    second_a = _item("a", "claude.hook", b"2")
    first_b = _item("b", "claude.hook", b"3")
    latest_b = _item("b", "claude.hook", b"4")
    third_a = _item("a", "claude.hook", b"5")
    for item in (first_a, second_a, first_b, latest_b, third_a):
        queue.push(item)
    assert queue.depth == 2
    assert queue.lost("b") == 2
    assert queue.needs_resync("b")
    assert queue.coalesced("b") == latest_b
    assert queue.pop() == first_a
    assert queue.pop() == second_a
    assert queue.depth == 0
    # The overflow item of "b" comes first (first overflowed), and taking it clears that tab's loss and resync.
    assert queue.pop() == latest_b
    assert queue.lost("b") == 0
    assert not queue.needs_resync("b")
    assert queue.pop() == third_a
    assert queue.lost("a") == 0
    assert queue.pop() is None


def test_round6_k25_ingest_queue_rejects_a_zero_limit() -> None:
    with pytest.raises(ValueError, match="positive"):
        _ = IngestQueue(limit=0)


def test_round6_k25_hook_event_names_for_each_kind() -> None:
    claude = _item("t", "claude.hook", b'{"session_id":"s","hook_event_name":"Stop"}')
    assert hook_event(claude) == "Stop"
    codex = _item("t", "codex.hook", b'{"session_id":"s","hook_event_name":"PermissionRequest"}')
    assert hook_event(codex) == "PermissionRequest"
    notify = _item("t", "codex.notify", b'{"type":"agent-turn-complete","thread_id":"x"}')
    assert hook_event(notify) == "agent-turn-complete"
    statusline = _item("t", "claude.statusline", b'{"session_id":"s"}')
    assert hook_event(statusline) is None
    assert hook_event(_item("t", "claude.hook", b"not json")) is None
    assert hook_event(_item("t", "claude.hook", b'{"session_id":"s"}')) is None
    assert hook_event(_item("t", "codex.notify", b'{"thread_id":"x"}')) is None
    assert hook_event(_item("t", "claude.other", b'{"hook_event_name":"Stop"}')) is None


def test_round6_k25_token_book_register_check_and_drop() -> None:
    book = TokenBook()
    book.register("tab-1", "secret-1")
    assert book.check("Bearer secret-1", "tab-1") is Decision.OK
    assert book.check("Bearer secret-1", "tab-2") is Decision.MISMATCH
    assert book.check("Bearer other", "tab-1") is Decision.UNAUTHORIZED
    assert book.check(None, "tab-1") is Decision.UNAUTHORIZED
    book.drop("tab-1")
    assert book.check("Bearer secret-1", "tab-1") is Decision.UNAUTHORIZED
    issued = book.issue("tab-3")
    assert book.check(f"Bearer {issued}", "tab-3") is Decision.OK
    with pytest.raises(ValueError, match="single path segment"):
        book.register("a/b", "token")
    with pytest.raises(ValueError, match="must not be empty"):
        book.register("tab-4", "")


# --- one daemon, in-process, over the real socket and HTTP -----------------------------------------------


@final
class _Ui:
    """A UI client on the real socket. A pump task routes replies by id and queues `$/event` notifications."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        self._replies: dict[int, asyncio.Future[ResponseOk | ResponseErr]] = {}
        self._events: asyncio.Queue[EventEnvelope] = asyncio.Queue()
        self._next_id = 1
        self._pump = asyncio.create_task(self._read(reader))

    async def _read(self, reader: asyncio.StreamReader) -> None:
        while True:
            line = await reader.readline()
            if line == b"":
                return
            frame = decode_envelope(line)
            if isinstance(frame, Notification):
                self._events.put_nowait(msgspec.json.decode(frame.params, type=EventEnvelope))
            elif isinstance(frame, (ResponseOk, ResponseErr)) and frame.id is not None:
                waiting = self._replies.pop(frame.id, None)
                if waiting is not None:
                    waiting.set_result(frame)

    async def call(self, method: str, params: msgspec.Struct | None = None) -> ResponseOk | ResponseErr:
        request_id = self._next_id
        self._next_id += 1
        payload = b"{}" if params is None else _ENCODER.encode(params)
        waiting: asyncio.Future[ResponseOk | ResponseErr] = asyncio.get_running_loop().create_future()
        self._replies[request_id] = waiting
        self._writer.write(_request(request_id, method, payload))
        await self._writer.drain()
        return await asyncio.wait_for(waiting, _TIMEOUT_S)

    async def event_of[E: UiEvent](self, kind: type[E]) -> E:
        """The next queued event of this kind. Other kinds are skipped, so a test waits for what it expects."""
        while True:
            envelope = await asyncio.wait_for(self._events.get(), _TIMEOUT_S)
            if isinstance(envelope.event, kind):
                return envelope.event

    async def close(self) -> None:
        self._writer.close()
        with contextlib.suppress(ConnectionError, OSError):
            await self._writer.wait_closed()
        _ = self._pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._pump


async def _connect_ui(path: Path) -> _Ui:
    reader, writer = await asyncio.open_unix_connection(path)
    ui = _Ui(reader, writer)
    initialized = await ui.call("initialize", _initialize(ClientRole.UI))
    assert isinstance(initialized, ResponseOk)
    subscribed = await ui.call("events/subscribe", SubscribeParams(after_seq=0))
    assert isinstance(subscribed, ResponseOk)
    return ui


async def _open_tab(ui: _Ui) -> TabView:
    found = await ui.call("tab/open", _open())
    assert isinstance(found, ResponseOk)
    return msgspec.json.decode(found.result, type=TabView)


def _post(port: int, tab: str, token: str, body: bytes) -> tuple[int, bytes]:
    """POST one Claude hook to the ingest route. Runs on a thread because http.client blocks."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=_TIMEOUT_S)
    try:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        connection.request("POST", f"/v1/tabs/{tab}/claude/hook", body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@dataclass(frozen=True, slots=True)
class _Core:
    daemon: Daemon
    ports: FakeTabPorts
    task: asyncio.Task[int]


@contextlib.asynccontextmanager
async def _running(root: Path, *, idle_s: float) -> AsyncGenerator[_Core]:
    sock = root / "core.sock"
    daemon = Daemon(sock, idle_s=idle_s)
    daemon.serve_ingest = True
    ports = use_fake_tabs(daemon, root)
    task = asyncio.create_task(daemon.serve())
    try:
        await wait_for_socket(sock)
        yield _Core(daemon=daemon, ports=ports, task=task)
    finally:
        if not task.done():
            daemon.request_stop()
            _ = await asyncio.wait_for(task, _TIMEOUT_S)


async def _until(ready: Callable[[], bool]) -> None:
    for _ in range(500):
        if ready():
            return
        await asyncio.sleep(0.01)
    pytest.fail("condition did not hold within 5 seconds")


async def _permission_flow(root: Path) -> None:
    async with _running(root, idle_s=30) as core:
        ui = await _connect_ui(root / "core.sock")
        try:
            opened = await _open_tab(ui)
            added = await ui.event_of(TabAdded)
            assert added.tab.id == opened.id
            tab = str(opened.id)
            port = core.daemon.ingest_port
            token = core.daemon.tabs.ingest_token(tab)
            assert port is not None
            assert token is not None
            status, _body = await asyncio.to_thread(_post, port, tab, token, _PERMISSION)
            assert status == 200
            updated = await ui.event_of(TabUpdated)
            assert updated.tab.status is TabStatus.WAITING_APPROVAL
            assert updated.tab.attention is True
            assert "status" in updated.changed
            status, body = await asyncio.to_thread(_post, port, tab, secrets.token_hex(32), _PERMISSION)
            assert status == 401
            assert msgspec.json.decode(body, type=_Problem).reason == "INGEST_UNAUTHORIZED"
            other = await _open_tab(ui)
            other_token = core.daemon.tabs.ingest_token(str(other.id))
            assert other_token is not None
            status, body = await asyncio.to_thread(_post, port, tab, other_token, _PERMISSION)
            assert status == 403
            assert msgspec.json.decode(body, type=_Problem).reason == "INGEST_TAB_MISMATCH"
        finally:
            await ui.close()


def test_round6_k25_permission_hook_reaches_the_ui_over_http(tmp_path: Path) -> None:
    asyncio.run(asyncio.wait_for(_permission_flow(tmp_path), _FLOW_S))


async def _running_tab_holds_the_daemon(root: Path) -> int:
    async with _running(root, idle_s=0.3) as core:
        first = await _connect_ui(root / "core.sock")
        opened = await _open_tab(first)
        started = await first.call("tab/started", StartedParams(tab_id=opened.id, pid=_PID))
        assert isinstance(started, ResponseOk)
        await first.close()
        # The tab is running, so the core stays up after its last client leaves, far past the 0.3 s idle window.
        await asyncio.sleep(0.6)
        assert not core.task.done()
        second = await _connect_ui(root / "core.sock")
        exited = await second.call("tab/exited", ExitedParams(tab_id=opened.id, wait_status=0))
        assert isinstance(exited, ResponseOk)
        await second.close()
        return await asyncio.wait_for(core.task, _TIMEOUT_S)


def test_round6_k25_running_tab_holds_the_daemon_until_it_exits(tmp_path: Path) -> None:
    assert asyncio.run(asyncio.wait_for(_running_tab_holds_the_daemon(tmp_path), _FLOW_S)) == 0


async def _slow_close_leaves_the_loop_free(root: Path) -> None:
    async with _running(root, idle_s=30) as core:
        ui = await _connect_ui(root / "core.sock")
        try:
            opened = await _open_tab(ui)
            started = await ui.call("tab/started", StartedParams(tab_id=opened.id, pid=_PID))
            assert isinstance(started, ResponseOk)
            gate = threading.Event()
            core.ports.gate = gate
            try:
                closing = asyncio.create_task(ui.call("tab/close", CloseTabParams(tab_id=opened.id)))
                # The stop wait (3 s on the real ports) now holds a worker thread; the loop must keep running.
                await _until(lambda: len(core.ports.pauses) > 0)
                began = time.perf_counter()
                await asyncio.sleep(0.05)
                lag = time.perf_counter() - began
            finally:
                gate.set()
            reply = await asyncio.wait_for(closing, _TIMEOUT_S)
            assert isinstance(reply, ResponseOk)
            assert msgspec.json.decode(reply.result, type=CloseTabResult).closed is True
            assert lag < _LAG_S
        finally:
            await ui.close()


def test_round6_k25_a_slow_tab_port_call_does_not_block_the_loop(tmp_path: Path) -> None:
    asyncio.run(asyncio.wait_for(_slow_close_leaves_the_loop_free(tmp_path), _FLOW_S))
