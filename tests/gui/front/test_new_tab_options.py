"""Task context, opening feedback and project removal through native widgets."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import msgspec
import pytest

from aconsole.contracts.app import AppliedResult, EmptyResult, SessionListResult
from aconsole.contracts.common import ApprovalDecision, WorkspaceMode
from aconsole.contracts.work_sessions import WorkSessionListResult
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
from aconsole.ui.viewmodels.approvals_vm import ApprovalsViewModel
from aconsole.ui.views.approvals.panel import ApprovalPanel
from aconsole.ui.views.tabs.new_tab import NewTabDialog
from aconsole.ui.views.tabs.project_bar import ProjectBar
from aconsole.ui.views.tabs.sidebar import TabSidebar
from tests.gui.front.test_b2_tabs import _model, _run_ui, _until

if TYPE_CHECKING:
    from tests.gui.front.test_b2_tabs import _TabsTestApp


@pytest.mark.parametrize("mode", list(WorkspaceMode))
def test_new_tab_passes_workspace_base_and_plan_context(mode: WorkspaceMode) -> None:
    async def scenario(app: _TabsTestApp) -> None:
        snapshot = demo_snapshot()
        project = snapshot.projects[0]
        plan = msgspec.structs.replace(snapshot.plans[0], project_id=project.id)
        snapshot = msgspec.structs.replace(snapshot, plans=(plan,), active_project=project.id)
        client, tabs = _model(snapshot)
        calls = []
        opened = []
        errors = []
        pending = asyncio.get_running_loop().create_future()

        async def request(method: str, params: msgspec.Struct, _result: type[msgspec.Struct]) -> msgspec.Struct:
            if method == "workSession/list":
                return WorkSessionListResult(sessions=())
            if method == "session/list":
                return SessionListResult(sessions=())
            assert method == "tab/open"
            calls.append(params)
            return await pending

        client.request = request
        dialog = NewTabDialog(tabs, opened.append, errors.append)
        assert app.window is not None
        dialog.present(app.window)
        modes = (WorkspaceMode.NEW_WORKTREE, WorkspaceMode.MAIN_CHECKOUT, WorkspaceMode.EXISTING_WORKTREE)
        dialog.workspace_row.set_selected(modes.index(mode))
        dialog.existing_path.set_text("/repo/existing")
        dialog.base_branch.set_text("release/next")
        dialog.node_row.set_selected(1)
        assert dialog.base_branch.get_visible() == (mode is WorkspaceMode.NEW_WORKTREE)
        dialog.open_button.emit("clicked")
        await _until(lambda: bool(calls))
        assert not dialog.body.get_sensitive()
        assert dialog.spinner.get_visible()
        assert not dialog.open_button.get_sensitive()
        dialog.open_button.emit("clicked")
        assert len(calls) == 1
        assert calls[0].workspace is mode
        assert calls[0].plan_node == plan.nodes[0].id
        assert calls[0].base_branch == ("release/next" if mode is WorkspaceMode.NEW_WORKTREE else None)
        assert calls[0].existing_worktree == ("/repo/existing" if mode is WorkspaceMode.EXISTING_WORKTREE else None)
        pending.set_exception(RuntimeError("branch does not exist"))
        await _until(lambda: not dialog._opening)
        assert dialog.body.get_sensitive()
        assert not dialog.spinner.get_visible()
        assert "branch does not exist" in dialog.hint.get_label()
        assert len(errors) == 1
        assert not opened
        assert dialog.base_branch.get_text() == "release/next"
        assert dialog.open_button.get_sensitive()
        dialog.close()
        await client.close()

    _run_ui(scenario)


def test_project_removal_is_disabled_with_tabs_and_reports_server_refusal() -> None:
    async def scenario(app: _TabsTestApp) -> None:
        snapshot = demo_snapshot()
        client, tabs = _model(snapshot)
        bar = ProjectBar(tabs, TabSidebar(tabs))
        assert app.window is not None
        app.window.set_content(bar)
        app.window.present()
        project = bar._selected()
        assert project is not None
        assert not bar.remove_button.get_sensitive()
        client.store.apply_snapshot(msgspec.structs.replace(snapshot, tabs=()), 1)
        assert bar.remove_button.get_sensitive()
        calls = []

        async def request(method: str, params: msgspec.Struct, _result: type[msgspec.Struct]) -> msgspec.Struct:
            calls.append((method, params))
            message = "project gained a tab"
            raise RuntimeError(message)

        client.request = request
        await bar._remove_project(project)
        assert calls[0][0] == "project/remove"
        assert calls[0][1].project_id == project.id
        assert bar.error_label.get_visible()
        assert "project gained a tab" in bar.error_label.get_label()
        assert bar._selected() == project
        assert bar.remove_button.get_sensitive()
        await client.close()

    _run_ui(scenario)


def test_deny_sends_message_but_allow_does_not() -> None:
    async def scenario(app: _TabsTestApp) -> None:
        snapshot = demo_snapshot()
        client, _tabs = _model(snapshot)
        calls = []

        async def request(method: str, params: msgspec.Struct, _result: type[msgspec.Struct]) -> msgspec.Struct:
            calls.append((method, params))
            return AppliedResult(applied=True)

        client.request = request
        panel = ApprovalPanel(ApprovalsViewModel(client), on_toast=lambda _text: None,
            on_open=lambda _tab: None, on_left=lambda: None)
        assert app.window is not None
        app.window.set_content(panel)
        app.window.present()
        view = snapshot.approvals[0]
        panel.update((view,), {}, view.tab_id)
        panel._guard_until = 0
        card = panel.cards[0]
        card.deny_message.set_text("  Use a temporary directory instead  ")
        card.deny_button.emit("clicked")
        await _until(lambda: bool(calls))
        assert calls[0][0] == "approval/decide"
        assert calls[0][1].decision is ApprovalDecision.DENY
        assert calls[0][1].message == "Use a temporary directory instead"
        other = msgspec.structs.replace(view, id=snapshot.approvals[1].id)
        panel.update((other,), {}, other.tab_id)
        panel._guard_until = 0
        card = panel.cards[0]
        card.deny_message.set_text("Do not send this on Allow")
        panel.decide(card, ApprovalDecision.ALLOW)
        await _until(lambda: len(calls) == 2)
        assert calls[1][1].message is None
        panel.stop()
        await client.close()

    _run_ui(scenario)


def test_remove_confirmation_captures_project_and_cancel_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from gi.repository import Adw

    dialogs = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda dialog, _parent: dialogs.append(dialog))

    async def scenario(app: _TabsTestApp) -> None:
        snapshot = msgspec.structs.replace(demo_snapshot(), tabs=())
        client, tabs = _model(snapshot)
        bar = ProjectBar(tabs, TabSidebar(tabs))
        assert app.window is not None
        app.window.set_content(bar)
        app.window.present()
        calls = []

        async def request(method: str, params: msgspec.Struct, _result: type[msgspec.Struct]) -> msgspec.Struct:
            calls.append((method, params))
            return EmptyResult()

        client.request = request
        original = bar._selected()
        assert original is not None
        bar.remove_button.emit("clicked")
        dialogs[-1].emit("response", "cancel")
        assert not calls
        bar.remove_button.emit("clicked")
        client.store.apply_snapshot(msgspec.structs.replace(snapshot, active_project=snapshot.projects[1].id), 1)
        dialogs[-1].emit("response", "remove")
        await _until(lambda: bool(calls))
        assert calls[0][0] == "project/remove"
        assert calls[0][1].project_id == original.id
        await client.close()

    _run_ui(scenario)
