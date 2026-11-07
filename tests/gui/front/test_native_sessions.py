"""Native CLI conversations stay distinct from task sessions in the new-tab dialog."""

from __future__ import annotations

from typing import TYPE_CHECKING

import msgspec

from aconsole.contracts.app import SessionInfo, SessionListResult
from aconsole.contracts.common import AgentKind, WorkspaceMode
from aconsole.contracts.work_sessions import WorkSessionListResult
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
from aconsole.ui.views.tabs.new_tab import NewTabDialog
from tests.gui.front.test_b2_tabs import _model, _run_ui, _until

if TYPE_CHECKING:
    from tests.gui.front.test_b2_tabs import _TabsTestApp


def test_new_tab_selects_native_cli_identity_and_working_directory() -> None:
    async def scenario(app: _TabsTestApp) -> None:
        snapshot = demo_snapshot()
        snapshot = msgspec.structs.replace(snapshot, agents=tuple(
            msgspec.structs.replace(agent, capabilities=agent.capabilities | {"resume", "fork"})
            for agent in snapshot.agents
        ))
        client, tabs = _model(snapshot)
        project = snapshot.projects[0]
        opened = []
        errors = []
        calls = []
        at = snapshot.tabs[0].status_since
        native = SessionInfo(session_id="native-a", title="Saved CLI conversation", started_at=at,
            updated_at=at, cwd=project.root, branch="main", preview=None)

        async def request(method: str, params: msgspec.Struct, _result_type: type[msgspec.Struct]) -> msgspec.Struct:
            if method == "session/list":
                return SessionListResult(sessions=(native,))
            if method == "workSession/list":
                return WorkSessionListResult(sessions=())
            assert method == "tab/open"
            calls.append(params)
            return snapshot.tabs[0]

        client.request = request
        dialog = NewTabDialog(tabs, opened.append, errors.append)
        assert app.window is not None
        dialog.present(app.window)
        await _until(lambda: len(dialog._native_sessions) == 1)
        dialog.native_session_row.set_selected(1)
        assert dialog.workspace_row.get_selected() == 2
        assert dialog.existing_path.get_text() == project.root
        dialog.open_button.emit("clicked")
        await _until(lambda: bool(opened))
        assert not errors
        assert len(calls) == 1
        assert calls[0].resume_session == "native-a"
        assert calls[0].workspace is WorkspaceMode.EXISTING_WORKTREE
        assert calls[0].existing_worktree == project.root
        await client.close()

    _run_ui(scenario)


def test_cursor_hides_native_resume_choice() -> None:
    async def scenario(app: _TabsTestApp) -> None:
        snapshot = demo_snapshot()
        agents = (
            *(agent for agent in snapshot.agents if agent.kind is not AgentKind.CURSOR),
            msgspec.structs.replace(snapshot.agents[0], kind=AgentKind.CURSOR, display_name="Cursor",
                available=True, capabilities=frozenset({"mcp_http"})),
        )
        client, tabs = _model(msgspec.structs.replace(snapshot, agents=agents))

        async def request(method: str, _params: msgspec.Struct, _result_type: type[msgspec.Struct]) -> msgspec.Struct:
            if method == "session/list":
                return SessionListResult(sessions=())
            return WorkSessionListResult(sessions=())

        client.request = request
        dialog = NewTabDialog(tabs, lambda _tab: None, lambda _error: None)
        assert app.window is not None
        dialog.present(app.window)
        assert any(agent.kind is AgentKind.CURSOR for agent in dialog._agents)
        for index, agent in enumerate(dialog._agents):
            if agent.kind is AgentKind.CURSOR:
                dialog.agent_row.set_selected(index)
                assert not dialog.native_session_row.get_visible()
                break
        dialog.close()
        await client.close()

    _run_ui(scenario)


def test_fork_menu_action_tracks_native_session_identity() -> None:
    from gi.repository import Gio, GLib

    from aconsole.contracts.events import TabUpdated
    from aconsole.contracts.ipc import EventEnvelope
    from aconsole.ui.views.tabs.row import TabRow

    async def scenario(app: _TabsTestApp) -> None:
        snapshot = demo_snapshot()
        source = msgspec.structs.replace(snapshot.tabs[0], agent_session_id="source-session")
        agents = tuple(msgspec.structs.replace(agent, capabilities=agent.capabilities | {"fork"})
            for agent in snapshot.agents)
        snapshot = msgspec.structs.replace(snapshot, agents=agents, tabs=(source, *snapshot.tabs[1:]))
        client, tabs = _model(snapshot)
        row = TabRow(tabs)
        item = next(tabs.model.get_item(index) for index in range(tabs.model.get_n_items())
            if str(tabs.model.get_item(index).tab_id) == str(source.id))
        row.bind_item(item)
        assert app.window is not None
        requested = []
        action = Gio.SimpleAction.new("fork-tab", GLib.VariantType.new("s"))
        action.connect("activate", lambda _action, parameter: requested.append(parameter.get_string()))
        app.window.add_action(action)
        app.window.set_content(row)
        app.window.present()
        await _until(row.get_mapped)
        assert row._fork_action.get_enabled()
        row._fork_action.activate(None)
        assert requested == [str(source.id)]
        changed = msgspec.structs.replace(source, agent_session_id=None)
        client.store.apply(EventEnvelope(seq=1, at=source.status_since,
            event=TabUpdated(tab=changed, changed=frozenset({"agent_session_id"}))))
        assert not row._fork_action.get_enabled()
        row.unbind_item()
        await client.close()

    _run_ui(scenario)
