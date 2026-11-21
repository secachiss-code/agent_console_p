"""Plan review tracks each pending node and submits a single human decision."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import msgspec

from aconsole.contracts.app import PlanNodeDetailsView, PlanNodeView, SetNodeStatusParams
from aconsole.contracts.common import NodeId, NodeStatus
from aconsole.contracts.events import PlanUpdated
from aconsole.contracts.ipc import EventEnvelope
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
from aconsole.ui.viewmodels.tabs_vm import TabsViewModel
from aconsole.ui.views.sidebar.plan_graph import PlanGraph
from aconsole.ui.views.window import MainWindow
from tests.gui.front.test_b10_views import _run_ui, _until

# pyright: reportPrivateUsage=false

if TYPE_CHECKING:
    from aconsole.contracts.app import PlanView
    from tests.gui.front.test_b10_views import _ViewTestApp


def _review_plan() -> PlanView:
    plan = demo_snapshot().plans[0]
    nodes = tuple(
        msgspec.structs.replace(
            node,
            status=NodeStatus.VERIFYING,
            depends_on=(),
            evidence="Full report <passed>\n" + "long_unbroken_report_" * 40,
        )
        for node in plan.nodes[:2]
    )
    return msgspec.structs.replace(plan, nodes=nodes, total=2, done=0)


def test_busy_nodes_do_not_disable_other_reviews_or_send_duplicate_requests() -> None:  # noqa: PLR0915
    async def scenario(app: _ViewTestApp) -> None:  # noqa: PLR0915
        plan = _review_plan()
        first, second = plan.nodes
        snapshot = msgspec.structs.replace(demo_snapshot(), plans=(plan,), active_project=plan.project_id)
        app.client.store.apply_snapshot(snapshot, 0)
        window = MainWindow(app, TabsViewModel(app.client))
        app.window = window
        window.present()
        window._open_plan(plan.id)
        page = window.plan_page
        assert page is not None
        errors: list[str] = []

        def tab_error(message: str) -> None:
            errors.append(message)

        window._tab_error = tab_error
        requests: list[SetNodeStatusParams] = []
        pending: list[asyncio.Future[PlanNodeView]] = []
        original = app.client.request

        async def request(method: str, params: msgspec.Struct, result_type: type[msgspec.Struct]) -> msgspec.Struct:
            if method == "plan/nodeDetails":
                return PlanNodeDetailsView(lines=())
            if method != "plan/setNodeStatus":
                return await original(method, params, result_type)
            assert isinstance(params, SetNodeStatusParams)
            requests.append(params)
            future: asyncio.Future[PlanNodeView] = asyncio.get_running_loop().create_future()
            pending.append(future)
            return await future

        app.client.request = request
        page.select_node(first.id)
        assert "Full report <passed>" in page.details_label.get_text()
        page.accept_button.emit("clicked")
        page.accept_button.emit("clicked")
        await _until(lambda: len(requests) == 1)
        assert requests[0].status is NodeStatus.DONE
        page.select_node(second.id)
        assert page.send_back_button.get_sensitive()
        page.send_back_button.emit("clicked")
        await _until(lambda: len(requests) == 2)
        assert requests[1].status is NodeStatus.PENDING
        assert requests[1].note
        done = msgspec.structs.replace(first, status=NodeStatus.DONE)
        pending[0].set_result(done)
        await _until(lambda: first.id not in page._pending_decisions)
        assert not page.send_back_button.get_sensitive()
        changed = msgspec.structs.replace(plan, nodes=(done, second), done=1)
        assert app.client.store.apply(EventEnvelope(seq=1, at=plan.updated_at, event=PlanUpdated(plan=changed)))
        assert window.plan_page is page
        pending[1].set_exception(RuntimeError("disk rejected"))
        await _until(lambda: second.id not in page._pending_decisions)
        assert page._nodes[second.id].status is NodeStatus.VERIFYING
        assert page.send_back_button.get_sensitive()
        assert len(errors) == 1
        assert "disk rejected" in errors[0]
        assert len(requests) == 2, "send-back must not have a second failed-to-pending RPC"
        await app.client.close()

    _run_ui(scenario)


def test_late_failure_finishes_the_original_page_only() -> None:
    async def scenario(app: _ViewTestApp) -> None:
        plan = _review_plan()
        node = plan.nodes[0]
        app.client.store.apply_snapshot(msgspec.structs.replace(demo_snapshot(), plans=(plan,)), 0)
        window = MainWindow(app, TabsViewModel(app.client))
        app.window = window
        window.present()
        old = PlanGraph(plan, lambda: None, on_decide=window._decide_node)
        window.plan_page = old
        original = app.client.request
        release: asyncio.Future[PlanNodeView] = asyncio.get_running_loop().create_future()
        started: list[msgspec.Struct] = []

        async def request(method: str, params: msgspec.Struct, result_type: type[msgspec.Struct]) -> msgspec.Struct:
            if method != "plan/setNodeStatus":
                return await original(method, params, result_type)
            started.append(params)
            return await release

        app.client.request = request
        old.select_node(node.id)
        old.accept_button.emit("clicked")

        # Change the page before the scheduled coroutine even starts.
        def ignore_decision(_node_id: NodeId, _accept: bool) -> None:  # noqa: FBT001
            pass

        replacement = PlanGraph(plan, lambda: None, on_decide=ignore_decision)
        replacement.select_node(node.id)
        replacement.accept_button.emit("clicked")
        window.plan_page = replacement
        await _until(lambda: bool(started))
        release.set_exception(RuntimeError("old request rejected"))
        await _until(lambda: not old._pending_decisions)
        assert node.id in replacement._pending_decisions
        assert not replacement.accept_button.get_sensitive()
        await app.client.close()

    _run_ui(scenario)


def test_accept_updates_open_graph_from_core_reply_without_waiting_for_event() -> None:
    async def scenario(app: _ViewTestApp) -> None:
        from datetime import timedelta

        from aconsole.contracts.app import PlanInboxResult

        plan = _review_plan()
        node = plan.nodes[0]
        done_node = msgspec.structs.replace(node, status=NodeStatus.DONE)
        fresh = msgspec.structs.replace(
            plan, done=1, nodes=(done_node, plan.nodes[1]), updated_at=plan.updated_at + timedelta(seconds=1)
        )
        latest = plan
        original = app.client.request

        async def request(method: str, params: msgspec.Struct, result_type: type[msgspec.Struct]) -> msgspec.Struct:
            nonlocal latest
            if method == "plan/get":
                return latest
            if method == "plan/inbox":
                return PlanInboxResult(entries=())
            if method == "plan/setNodeStatus":
                latest = fresh
                return done_node
            return await original(method, params, result_type)

        app.client.request = request
        app.client.store.apply_snapshot(msgspec.structs.replace(demo_snapshot(), plans=(plan,)), 0)
        window = MainWindow(app, TabsViewModel(app.client))
        app.window = window
        window.present()
        window._open_plan(plan.id)
        page = window.plan_page
        assert page is not None
        page.select_node(node.id)
        page.accept_button.emit("clicked")
        await _until(lambda: page.plan.done == 1)
        assert window.plan_page is page
        assert page._nodes[node.id].status is NodeStatus.DONE
        assert page.node_buttons[node.id].has_css_class("ac-plan-done")
        assert app.client.store.seq == 0, "RPC reconciliation must not invent an event sequence"
        await app.client.close()

    _run_ui(scenario)
