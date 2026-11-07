"""The real daemon discovers native CLI sessions and validates their project and agent."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import msgspec

from aconsole.contracts.app import (
    OpenTabParams,
    ProjectAddParams,
    ProjectView,
    SessionListParams,
    SessionListResult,
    TabIdParams,
    TabView,
    TerminalLaunchSpec,
)
from aconsole.contracts.common import AgentKind, WorkspaceMode
from aconsole.core.infrastructure.agents.claude.sessions import project_key
from aconsole.core.infrastructure.agents.launch import HostAgentCommands
from tests.core.test_claude_sessions import _write
from tests.core.test_tabs_restore import _row
from tests.core.test_wire import _connect, _daemon, _git_repo, _stop

if TYPE_CHECKING:
    from pathlib import Path


def test_native_sessions_reach_the_public_daemon(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    home = tmp_path / "home"
    folder = home / ".claude" / "projects" / project_key(str(repo))
    folder.mkdir(parents=True)
    _write(folder / "native-a.jsonl", "native-a", "Saved conversation", cwd=str(repo), mtime=1700000000)
    commands = HostAgentCommands(lambda _container: home)

    async def run() -> None:
        daemon = _daemon(tmp_path, serve_ingest=False)
        daemon.tabs.use_commands(commands)
        task = asyncio.create_task(daemon.serve())
        client = await _connect(daemon.socket)
        try:
            project = await client.call("project/add", ProjectAddParams(root=str(repo)), ProjectView)
            query = SessionListParams(project_id=project.id, agent=AgentKind.CLAUDE)
            found = await client.call("session/list", query, SessionListResult)
            assert [item.session_id for item in found.sessions] == ["native-a"]
            assert found.sessions[0].title == "Saved conversation"
            unsupported = await client.call("session/list", SessionListParams(
                project_id=project.id, agent=AgentKind.CURSOR,
            ), SessionListResult)
            assert unsupported.sessions == ()
            params = OpenTabParams(project_id=project.id, agent=AgentKind.CLAUDE,
                workspace=WorkspaceMode.MAIN_CHECKOUT, resume_session="native-a")
            tab = await client.call("tab/open", params, TabView)
            spec = await client.call("tab/launchSpec", TabIdParams(tab_id=tab.id), TerminalLaunchSpec)
            assert spec.argv[-2:] == ("--resume", "native-a")
            assert await client.error(
                "tab/open", msgspec.structs.replace(params, agent=AgentKind.CURSOR),
            ) == "AGENT_UNAVAILABLE"
            assert await client.error(
                "tab/open", msgspec.structs.replace(params, resume_session="foreign"),
            ) == "SESSION_NOT_FOUND"
            (folder / "native-a.jsonl").unlink()
            assert await client.error("tab/launchSpec", TabIdParams(tab_id=tab.id)) == "SESSION_NOT_FOUND"
        finally:
            await _stop(daemon, task, client)

    asyncio.run(run())


def test_fork_pending_is_saved_in_database(tmp_path: Path) -> None:
    from aconsole.core.infrastructure.persistence.engine import write_engine
    from aconsole.core.infrastructure.persistence.repositories.tabs import TabDatabase
    from aconsole.core.infrastructure.persistence.startup import prepare_database, sqlite_url
    from aconsole.core.infrastructure.persistence.writer import Writer

    path = tmp_path / "database.db"
    prepare_database(path, now=datetime.now(UTC))
    engine = write_engine(sqlite_url(path))
    writer = Writer(engine)
    writer.start()
    try:
        store = TabDatabase(writer)
        row = replace(_row("fork-tab", title="fork"), fork_pending=True)
        store.save(row)
        assert store.load_open()[0].fork_pending
        store.save(replace(row, session="child-session", fork_pending=False))
        restored = store.load_open()[0]
        assert restored.session == "child-session"
        assert not restored.fork_pending
    finally:
        writer.stop()
        engine.dispose()
