"""Tabs survive a core restart. Secrets stay out of the database."""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path

import msgspec
from sqlalchemy.orm import Session

from aconsole.contracts.app import (
    CloseTabParams,
    CloseTabResult,
    EmptyParams,
    OpenTabParams,
    ReorderParams,
    SnapshotResult,
    StartedParams,
    SubscribeParams,
    SubscribeResult,
    TabIdParams,
    TabView,
)
from aconsole.contracts.common import AgentKind, ClientRole, ProjectId, TabStatus, WorkspaceMode
from aconsole.contracts.events import TabAdded
from aconsole.contracts.ipc import (
    EventEnvelope,
    InitializeParams,
    Notification,
    ProtocolVersion,
    Request,
    ResponseErr,
    ResponseOk,
)
from aconsole.core.application.ports.tab_store import StoredTab, TabDirectory
from aconsole.core.application.services.tabs import ProjectSeed, TabService
from aconsole.core.bootstrap import Daemon
from aconsole.core.domain.clock import FrozenClock
from aconsole.core.infrastructure.ipc.framing import decode_envelope, encode_line
from aconsole.core.infrastructure.persistence.engine import write_engine
from aconsole.core.infrastructure.persistence.models import Setting
from aconsole.core.infrastructure.persistence.repositories.tabs import SETTINGS_KEY, TabDatabase
from aconsole.core.infrastructure.persistence.startup import prepare_database, sqlite_url
from aconsole.core.infrastructure.persistence.writer import Writer
from tests.core.conftest import FakeTabPorts, wait_for_socket

_ENCODER = msgspec.json.Encoder()
_GIT = "/usr/bin/git"
_WHEN = datetime(2026, 10, 27, tzinfo=UTC)
_WAIT_S = 15.0
_THREE = (("alpha", "kept"), ("beta", None), ("gamma", None))


def test_tabs_restore_after_restart(tmp_path: Path) -> None:
    """Three open tabs come back with the same title, order, and worktree, and the process is not started."""
    first, second, events, secret_kept = asyncio.run(_restart(tmp_path))
    assert [tab.title for tab in second] == [tab.title for tab in first]
    assert [tab.order for tab in second] == [tab.order for tab in first]
    assert [tab.worktree_path for tab in second] == [tab.worktree_path for tab in first]
    assert [tab.agent_session_id for tab in second] == [tab.agent_session_id for tab in first]
    assert [tab.started for tab in second] == [False, False, False]
    assert events == ("tab_added", "tab_added", "tab_added")
    assert secret_kept is False


def test_tabs_restore_worktrees_dir(tmp_path: Path) -> None:
    """Settings.worktrees_dir, not the XDG default, is the parent of a new worktree."""
    worktree, directory, tab_id = asyncio.run(_from_settings(tmp_path))
    assert worktree == directory / "repo" / tab_id
    assert not worktree.is_relative_to(tmp_path / "xdg-data")


def test_tabs_restore_dead_process_is_exited_and_launch_mints_tokens() -> None:
    """A tab that was working comes back exited, and launchSpec issues a token the store never saw."""
    ports = FakeTabPorts()
    store = _Memory()
    service = TabService(FrozenClock(_WHEN), ports)
    service.use_store(store)
    service.add_project(
        ProjectSeed(project_id="proj", root="/repo", container="agentbox", sessions=frozenset({"kept"}))
    )
    opened = service.open_tab(
        OpenTabParams(
            project_id=ProjectId("proj"),
            agent=AgentKind.CLAUDE,
            workspace=WorkspaceMode.MAIN_CHECKOUT,
            title="live",
            resume_session="kept",
        )
    )
    _ = service.started(StartedParams(tab_id=opened.id, pid=5))
    secret = service.ingest_token(str(opened.id))
    saved = store.rows[str(opened.id)]
    fresh = TabService(FrozenClock(_WHEN), ports)
    fresh.use_store(store)
    fresh.reload()
    view = fresh.snapshot().tabs[0]
    spec = fresh.launch_spec(TabIdParams(tab_id=view.id))
    assert secret is not None
    assert secret not in _blob(saved)
    assert saved.status == "working"
    assert store.rows[str(opened.id)].status == "exited"
    assert view.status is TabStatus.EXITED
    assert view.started is False
    assert view.agent_session_id == "kept"
    assert view.worktree_path == "/repo"
    assert spec.env["ACONSOLE_TAB_TOKEN"] != secret
    assert spec.env["ACONSOLE_SIDECAR_TOKEN"] != ""


def test_tabs_restore_repository_round_trip(tmp_path: Path) -> None:
    """The writer stores the tab and the worktree directory, and a close drops it from the open set."""
    database = tmp_path / "aconsole.db"
    directory = tmp_path / "wt"
    _ = prepare_database(database, now=_WHEN)
    engine = write_engine(sqlite_url(database))
    writer = Writer(engine)
    writer.start()
    try:
        store = TabDatabase(writer)
        _ = writer.call(lambda session: _put_settings(session, str(directory)))
        found = store.settings()
        row = _row("tab-1", title="alpha")
        store.save(row)
        store.save(_row("tab-1", title="beta"))
        loaded = store.load_open()
        store.remove("tab-1", closed_at=_WHEN, drop_worktree=True)
        after = store.load_open()
    finally:
        writer.stop()
        engine.dispose()
    assert found == TabDirectory(worktrees_dir=str(directory), rev=4)
    assert len(loaded) == 1
    assert loaded[0].title == "beta"
    assert loaded[0].container == "agentbox"
    assert loaded[0].session == "kept"
    assert loaded[0].worktree == "/wt/tab-1"
    assert after == ()


class _Memory:
    """In-memory `TabStore` for the service test. It records exactly what the service asked to save."""

    def __init__(self) -> None:
        self.rows: dict[str, StoredTab] = {}
        self.directory: TabDirectory = TabDirectory(worktrees_dir=None, rev=0)

    def save(self, tab: StoredTab) -> None:
        self.rows[tab.id] = tab

    def remove(self, tab_id: str, *, closed_at: datetime, drop_worktree: bool) -> None:
        _ = (closed_at, drop_worktree)
        _ = self.rows.pop(tab_id, None)

    def load_open(self) -> tuple[StoredTab, ...]:
        return tuple(self.rows[key] for key in sorted(self.rows))

    def settings(self) -> TabDirectory:
        return self.directory

    def save_setup(self, *, completed: bool, rev: int) -> None:
        self.directory = TabDirectory(
            worktrees_dir=self.directory.worktrees_dir,
            rev=rev,
            setup_completed=completed,
        )


async def _restart(tmp_path: Path) -> tuple[tuple[TabView, ...], tuple[TabView, ...], tuple[str, ...], bool]:
    repo = tmp_path / "repo"
    _init_repo(repo)
    database = tmp_path / "aconsole.db"
    sock = tmp_path / "core.sock"
    first, secret = await _open_three(sock, database, repo)
    second, events = await _read_back(sock, database)
    kept = tuple(sorted(first, key=lambda tab: tab.title))
    back = tuple(sorted(second, key=lambda tab: tab.title))
    return kept, back, events, _secret_in_db(database, secret)


async def _open_three(sock: Path, database: Path, repo: Path) -> tuple[tuple[TabView, ...], str]:
    daemon = Daemon(sock, idle_s=30, database=database)
    daemon.tabs.add_project(
        ProjectSeed(project_id="proj", root=str(repo), container="agentbox", sessions=frozenset({"kept"}))
    )
    task = asyncio.create_task(daemon.serve())
    await wait_for_socket(sock)
    secret = ""
    try:
        reader, writer = await asyncio.open_unix_connection(sock)
        try:
            await _init(reader, writer)
            opened: list[TabView] = []
            for title, session in _THREE:
                opened.append(await _open(reader, writer, title=title, session=session))
            _ = await _send(reader, writer, 6, "tab/reorder", ReorderParams(tab_id=opened[2].id, order=7))
            await _close_extra(reader, writer)
            first = _views(await _send(reader, writer, 8, "app/snapshot", EmptyParams()))
            token = daemon.tabs.ingest_token(str(opened[0].id))
            secret = "" if token is None else token
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        daemon.request_stop()
        _ = await asyncio.wait_for(task, _WAIT_S)
    return first, secret


async def _close_extra(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    gone = await _open(reader, writer, title="gone", session=None)
    closed = msgspec.json.decode(
        (await _send(reader, writer, 7, "tab/close", CloseTabParams(tab_id=gone.id))).result,
        type=CloseTabResult,
    )
    if closed.closed:
        return
    message = closed.dirty_summary or "tab stayed open"
    raise TypeError(message)


async def _read_back(sock: Path, database: Path) -> tuple[tuple[TabView, ...], tuple[str, ...]]:
    daemon = Daemon(sock, idle_s=30, database=database)
    task = asyncio.create_task(daemon.serve())
    await wait_for_socket(sock)
    try:
        reader, writer = await asyncio.open_unix_connection(sock)
        try:
            await _init(reader, writer)
            second = _views(await _send(reader, writer, 2, "app/snapshot", EmptyParams()))
            events = await _added(reader, writer)
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        daemon.request_stop()
        _ = await asyncio.wait_for(task, _WAIT_S)
    return second, events


async def _from_settings(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    _init_repo(repo)
    database = tmp_path / "aconsole.db"
    directory = tmp_path / "from-settings"
    _seed_dir(database, directory)
    sock = tmp_path / "core.sock"
    daemon = Daemon(sock, idle_s=30, database=database)
    daemon.tabs.add_project(ProjectSeed(project_id="proj", root=str(repo), container="agentbox"))
    task = asyncio.create_task(daemon.serve())
    await wait_for_socket(sock)
    try:
        reader, writer = await asyncio.open_unix_connection(sock)
        try:
            await _init(reader, writer)
            opened = await _open(reader, writer, title="one", session=None)
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        daemon.request_stop()
        _ = await asyncio.wait_for(task, _WAIT_S)
    return Path(opened.worktree_path), directory, str(opened.id)


def _views(found: ResponseOk) -> tuple[TabView, ...]:
    body = msgspec.json.decode(found.result, type=SnapshotResult)
    return body.snapshot.tabs


async def _added(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> tuple[str, ...]:
    found = await _send(reader, writer, 3, "events/subscribe", SubscribeParams(after_seq=0))
    subscribed = msgspec.json.decode(found.result, type=SubscribeResult)
    names: list[str] = []
    for _ in range(subscribed.current_seq):
        frame = decode_envelope(await asyncio.wait_for(reader.readuntil(b"\n"), _WAIT_S))
        if not isinstance(frame, Notification):
            continue
        body = msgspec.json.decode(frame.params, type=EventEnvelope)
        if isinstance(body.event, TabAdded):
            names.append("tab_added")
    return tuple(names)


async def _open(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    title: str,
    session: str | None,
) -> TabView:
    found = await _send(
        reader,
        writer,
        4,
        "tab/open",
        OpenTabParams(
            project_id=ProjectId("proj"),
            agent=AgentKind.CLAUDE,
            workspace=WorkspaceMode.NEW_WORKTREE,
            title=title,
            resume_session=session,
        ),
    )
    return msgspec.json.decode(found.result, type=TabView)


async def _init(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    params = InitializeParams(
        protocol=(ProtocolVersion(major=1, minor=0), ProtocolVersion(major=1, minor=0)),
        role=ClientRole.UI,
        client_name="test",
        client_version="0.0.0",
        pid=1,
        capabilities=frozenset({"events.v1"}),
    )
    _ = await _send(reader, writer, 1, "initialize", params)


async def _send(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request_id: int,
    method: str,
    params: msgspec.Struct,
) -> ResponseOk:
    payload = Request(id=request_id, method=method, params=msgspec.Raw(_ENCODER.encode(params)))
    writer.write(encode_line(_ENCODER, payload))
    await writer.drain()
    frame = decode_envelope(await asyncio.wait_for(reader.readuntil(b"\n"), _WAIT_S))
    if not isinstance(frame, ResponseOk):
        detail = frame.error.message if isinstance(frame, ResponseErr) else "empty frame"
        message = f"{method} failed: {detail}"
        raise TypeError(message)
    return frame


def _seed_dir(database: Path, directory: Path) -> None:
    _ = prepare_database(database, now=_WHEN)
    engine = write_engine(sqlite_url(database))
    try:
        with Session(engine) as session:
            session.add(Setting(key=SETTINGS_KEY, value={"worktrees_dir": str(directory)}, rev=4))
            session.commit()
    finally:
        engine.dispose()


def _put_settings(session: Session, directory: str) -> None:
    session.add(Setting(key=SETTINGS_KEY, value={"worktrees_dir": directory}, rev=4))


def _row(tab_id: str, *, title: str) -> StoredTab:
    return StoredTab(
        id=tab_id,
        project_id="proj",
        project_name="proj",
        project_root="/repo",
        container="agentbox",
        agent="claude",
        title=title,
        order=1,
        status="idle",
        status_detail=None,
        since=_WHEN,
        created_at=_WHEN,
        worktree=f"/wt/{tab_id}",
        branch="ac/claude",
        base_branch="main",
        dirty=False,
        workspace_mode="new_worktree",
        plan_node=None,
        session="kept",
    )


def _blob(tab: StoredTab) -> str:
    return " ".join(str(getattr(tab, field.name)) for field in fields(tab))


def _secret_in_db(database: Path, secret: str) -> bool:
    if secret == "":
        return True
    needle = secret.encode()
    return any(needle in path.read_bytes() for path in database.parent.glob(f"{database.name}*") if path.is_file())


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _ = (repo / "README").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "-c", "user.email=aconsole@localhost", "-c", "user.name=aconsole", "commit", "-m", "init")


def _git(repo: Path, *args: str) -> None:
    _ = subprocess.run([_GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)
