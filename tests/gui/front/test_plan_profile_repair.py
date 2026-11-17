"""Frozen profile repair remains explicit, preserves the document, and can be retried."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import msgspec

from aconsole.contracts.common import NodeStatus
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
from aconsole.ui.viewmodels.tabs_vm import TabsViewModel
from aconsole.ui.views.sidebar.plan_graph import PlanGraph
from aconsole.ui.views.sidebar.plan_repair import PlanRepairDialog
from aconsole.ui.views.window import MainWindow
from tests.gui.front.test_b10_views import _run_ui, _until

if TYPE_CHECKING:
    from aconsole.contracts.app import PlanView
    from tests.gui.front.test_b10_views import _ViewTestApp


def _frozen() -> PlanView:
    plan = demo_snapshot().plans[0]
    node = msgspec.structs.replace(plan.nodes[0], level=0, status=NodeStatus.PENDING, tab_id=None)
    return msgspec.structs.replace(plan, phase="frozen", nodes=(node,), total=1, done=0)


def test_repair_button_is_only_available_before_execution() -> None:
    async def scenario(app: _ViewTestApp) -> None:
        plan = _frozen()
        opened = []
        page = PlanGraph(plan, lambda: None, on_repair=opened.append)
        page.repair_button.emit("clicked")
        assert opened == [plan]
        assert page.update(msgspec.structs.replace(plan, nodes=(msgspec.structs.replace(plan.nodes[0], attempt_no=1),)))
        assert not page.repair_button.get_visible()
        page.repair_button.emit("clicked")
        assert opened == [plan]
        assert page.update(msgspec.structs.replace(plan, phase="draft"))
        assert not page.repair_button.get_visible()
        await app.client.close()

    _run_ui(scenario)


def test_repair_sends_only_profiles_and_keeps_errors_and_input_for_retry() -> None:
    async def scenario(app: _ViewTestApp) -> None:
        plan = _frozen()
        app.client.store.apply_snapshot(msgspec.structs.replace(demo_snapshot(), plans=(plan,)), 0)
        window = MainWindow(app, TabsViewModel(app.client))
        app.window = window
        window.present()
        window._open_plan(plan.id)
        page = window.plan_page
        assert page is not None
        page.repair_button.emit("clicked")
        dialog = window.get_visible_dialog()
        assert isinstance(dialog, PlanRepairDialog)
        requests = []
        pending = []

        async def request(method: str, params: msgspec.Struct, result_type: type[msgspec.Struct]) -> msgspec.Struct:
            assert method == "plan/save"
            assert result_type is type(plan)
            requests.append(params)
            future = asyncio.get_running_loop().create_future()
            pending.append(future)
            return await future

        app.client.request = request
        for invalid in ("broken JSON", "[]", "{}", '{"IDX-0": []}'):
            dialog.editor.get_buffer().set_text(invalid)
            dialog.apply_button.emit("clicked")
            assert dialog.error_label.get_visible()
            assert not requests
        raw = '{"IDX-0": {"micro": {"tests": ["python", "-m", "pytest"]}}}'
        dialog.editor.get_buffer().set_text(raw)
        dialog.apply_button.emit("clicked")
        dialog.apply_button.emit("clicked")
        await _until(lambda: len(requests) == 1)
        params = requests[0]
        assert (params.plan_id, params.project_id, params.title, params.goal) == (
            plan.id,
            plan.project_id,
            plan.title,
            plan.goal,
        )
        assert params.nodes is None
        assert set(params.repair_gate_profiles) == {"IDX-0"}
        pending[0].set_exception(RuntimeError("NODE_CONTRACT: missing lint"))
        await _until(dialog.apply_button.get_sensitive)
        assert "missing lint" in dialog.error_label.get_text()
        buffer = dialog.editor.get_buffer()
        assert buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), include_hidden_chars=True) == raw
        dialog.apply_button.emit("clicked")
        await _until(lambda: len(requests) == 2)
        repaired = msgspec.structs.replace(plan, goal=plan.goal + " repaired")
        pending[1].set_result(repaired)
        await _until(lambda: page.plan == repaired)
        await app.client.close()

    _run_ui(scenario)
