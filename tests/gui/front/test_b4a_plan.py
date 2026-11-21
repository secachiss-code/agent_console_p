"""B4a plan card rendering and activation behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

import pytest

# pyright: reportPrivateUsage=false

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from aconsole.contracts.app import PlanView
    from aconsole.contracts.common import NodeId, PlanId
    from aconsole.ui.views.window import MainWindow

import gi
import msgspec

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from aconsole.contracts.common import NodeStatus  # noqa: E402
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.views.app import AConsoleApp  # noqa: E402
from aconsole.ui.views.sidebar.plan_graph import PlanGraph  # noqa: E402
from aconsole.ui.views.tabs.new_tab import NewTabDialog  # noqa: E402


def _demo_plan() -> PlanView:
    snapshot = demo_snapshot()
    assert snapshot.active_project is not None
    return next(
        plan for plan in snapshot.plans if plan.project_id == snapshot.active_project and plan.status == "active"
    )


def _in_gtk_context(check: Callable[[], None]) -> None:
    """Run widget assertions after Adw.Application registration on GTK's loop."""
    failures: list[BaseException] = []

    class _WidgetTestApp(Adw.Application):
        def __init__(self) -> None:
            super().__init__(
                application_id="io.github.aconsole.AConsole.B4aTest",
                flags=Gio.ApplicationFlags.NON_UNIQUE,
            )
            self.timeout_id: int | None = None
            self.timed_out: bool = False

        @override
        def do_activate(self) -> None:
            self.timeout_id = GLib.timeout_add_seconds(5, self._timeout)
            try:
                check()
            except BaseException as error:  # noqa: BLE001 - Preserve GTK assertion failures for the caller.
                failures.append(error)
            finally:
                if not self.timed_out:
                    _ = GLib.source_remove(self.timeout_id)
                    self.timeout_id = None
                self.quit()

        def _timeout(self) -> bool:
            self.timed_out = True
            failures.append(TimeoutError("B4a GUI assertion exceeded 5 seconds"))
            self.quit()
            return GLib.SOURCE_REMOVE

    app = _WidgetTestApp()
    status = run_app(lambda: app, ["aconsole-b4a-test"])
    assert status == 0
    if failures:
        raise failures[0]


def _run_window_probe(  # noqa: C901 - Keep lifecycle setup, timeout, and cleanup together for this GTK probe.
    running_app: str, check: Callable[[MainWindow], None]
) -> None:
    """Check a live fake-backed window and close its core client on every path."""
    failures: list[BaseException] = []

    class _WindowProbeApp(AConsoleApp):
        def __init__(self) -> None:
            super().__init__(CoreClient(running_app))
            self.watchdog_id: int | None = None
            self.poll_id: int | None = None

        @override
        def do_activate(self) -> None:
            super().do_activate()
            self.watchdog_id = GLib.timeout_add_seconds(10, self._watchdog)
            self.poll_id = GLib.timeout_add(10, self._check_window)

        def _check_window(self) -> bool:
            window = self.window
            if window is None:
                if self.failed:
                    failures.append(RuntimeError("AConsoleApp failed before creating MainWindow"))
                    self.poll_id = None
                return GLib.SOURCE_REMOVE if self.failed else GLib.SOURCE_CONTINUE
            self.poll_id = None
            try:
                check(window)
            except BaseException as error:  # noqa: BLE001 - Preserve GTK assertion failures for the caller.
                failures.append(error)
            finally:
                _ = window.emit("close-request")  # pyright: ignore[reportAny]
            return GLib.SOURCE_REMOVE

        def _watchdog(self) -> bool:
            self.watchdog_id = None
            failures.append(TimeoutError("B4a MainWindow probe exceeded 10 seconds"))
            window = self.window
            _ = window.emit("close-request") if window is not None else spawn_task(self._finish())
            return GLib.SOURCE_REMOVE

    app = _WindowProbeApp()
    status = run_app(lambda: app, ["aconsole-b4a-window-test"])
    if app.poll_id is not None:
        _ = GLib.source_remove(app.poll_id)
    if app.watchdog_id is not None:
        _ = GLib.source_remove(app.watchdog_id)
    assert status == 0
    if failures:
        raise failures[0]


def test_b4a_plan_card_demo_shows_plan_and_ready_nodes() -> None:
    from aconsole.ui.views.sidebar.plan_card import PlanCard

    plan = _demo_plan()

    def check() -> None:
        card = PlanCard(on_activate=lambda _plan_id: None)
        card.set_plan(plan)

        assert isinstance(card, Gtk.Button)
        assert card.plan_id == plan.id
        assert card.title_label.get_text()
        progress = card.progress_label.get_text()
        assert str(plan.done) in progress
        assert str(plan.total) in progress
        assert card.progress_label.has_css_class("numeric")
        ready_keys = [node.key for node in plan.nodes if node.ready]
        assert ready_keys
        assert all(key in card.detail_label.get_text() for key in ready_keys)

    _in_gtk_context(check)


def test_b4a_plan_card_activation_and_page() -> None:
    from aconsole.ui.views.sidebar.plan_card import PlanCard

    plan = _demo_plan()

    def check() -> None:
        activated: list[PlanId] = []
        card = PlanCard(on_activate=activated.append)
        card.set_plan(plan)

        card.emit("clicked")

        assert activated == [plan.id]

    _in_gtk_context(check)


def test_b4a_plan_card_empty_state_explains_missing_plan() -> None:
    from aconsole.ui.views.sidebar.plan_card import PlanCard

    def check() -> None:
        activated: list[PlanId] = []
        card = PlanCard(on_activate=activated.append)
        card.set_plan(None)

        assert card.plan_id is None
        assert card.title_label.get_text()
        assert card.detail_label.get_text().strip()
        card.emit("clicked")
        assert activated == []

    _in_gtk_context(check)


def test_b4a_plan_graph_shows_all_nodes_and_emphasizes_hovered_node_neighbors() -> None:
    import msgspec

    from aconsole.contracts.common import NodeId

    plan = _demo_plan()
    template = plan.nodes[0]
    nodes = tuple(
        msgspec.structs.replace(
            template,
            id=NodeId(f"graph-node-{index}"),
            key=f"N-{index:02}",
            title=f"Graph node {index}",
            layer=index // 10,
            depends_on=(),
        )
        for index in range(31)
    )
    large_plan = msgspec.structs.replace(plan, nodes=nodes, total=len(nodes))

    def check() -> None:
        assert len(PlanGraph(large_plan, lambda: None).node_buttons) == len(nodes)

        neighbor_source = msgspec.structs.replace(
            nodes[0],
            id=NodeId("neighbor-source"),
            key="N-source",
            title="Source",
            layer=0,
            depends_on=(),
        )
        connected = msgspec.structs.replace(
            nodes[1],
            id=NodeId("connected"),
            key="N-connected",
            title="Connected",
            layer=1,
            depends_on=(neighbor_source.id,),
        )
        unrelated = msgspec.structs.replace(
            nodes[2],
            id=NodeId("unrelated"),
            key="N-unrelated",
            title="Unrelated",
            layer=1,
            depends_on=(),
        )
        hover_plan = msgspec.structs.replace(plan, nodes=(neighbor_source, connected, unrelated), total=3)
        graph = PlanGraph(hover_plan, lambda: None)
        graph.set_hovered(connected.id)
        details = graph.details_label.get_text()
        assert connected.title in details or connected.key in details
        assert graph.node_buttons[connected.id].get_opacity() == 1.0
        assert graph.node_buttons[neighbor_source.id].get_opacity() == 1.0
        assert graph.node_buttons[unrelated.id].get_opacity() == pytest.approx(0.45, abs=0.01)

    _in_gtk_context(check)


def test_b4a_plan_graph_tab_action_targets_selected_nodes_tab() -> None:
    import msgspec

    from aconsole.contracts.common import TabId

    base = _demo_plan()
    tab_id = TabId("01J9Z5W6FX1M2A3B4C5D6E7F8G")
    node = msgspec.structs.replace(base.nodes[0], tab_id=tab_id)
    plan = msgspec.structs.replace(base, nodes=(node,))

    def check() -> None:
        opened: list[TabId] = []
        graph = PlanGraph(plan, lambda: None, opened.append)
        graph.select_node(node.id)
        assert graph.tab_button.get_sensitive()
        graph.tab_button.emit("clicked")
        assert opened == [tab_id]

    _in_gtk_context(check)


def test_b4a_window_plan_card_demo(running_app: str) -> None:
    """The live demo window mounts its active project's plan card in the sidebar."""
    plan = _demo_plan()

    def check(window: MainWindow) -> None:
        card = window.plan_card
        assert card.get_visible()
        assert card.plan_id == plan.id
        assert str(plan.done) in card.progress_label.get_text()
        assert str(plan.total) in card.progress_label.get_text()

    _run_window_probe(running_app, check)


def test_b4a_window_plan_card_opens_page(running_app: str) -> None:
    """Clicking the card puts the plan in place of the terminal; a tab or the back button returns."""
    plan = _demo_plan()

    def check(window: MainWindow) -> None:
        assert window.plan_page is None
        window.plan_card.emit("clicked")

        page = window.plan_page
        assert isinstance(page, PlanGraph)
        assert page.plan.id == plan.id
        assert window.content_view.get_content() is page

        page.close_button.emit("clicked")
        assert window.plan_page is None
        assert window.content_view.get_content() is window.terminal_stack

        window.plan_card.emit("clicked")
        assert window.content_view.get_content() is window.plan_page
        on_row_pressed = window.tabs_sidebar.on_row_pressed
        assert on_row_pressed is not None
        on_row_pressed()
        assert window.content_view.get_content() is window.terminal_stack

    _run_window_probe(running_app, check)


def test_window_actions_reach_the_core(running_app: str) -> None:
    """The close and new-tab controls have real handlers, not only the ones tests install."""

    def check(window: MainWindow) -> None:
        assert window.close_tab_action.get_enabled()
        dialog = NewTabDialog(window.tabs, lambda _tab: None, lambda _error: None)
        assert dialog.open_button.get_sensitive()

    _run_window_probe(running_app, check)


def test_a_summary_section_folds_and_remembers_it(tmp_path: Path) -> None:
    from aconsole.ui.client.ui_state import UiState
    from aconsole.ui.views.sidebar.plan_card import PlanCard
    from aconsole.ui.views.sidebar.section import SummarySection

    def check() -> None:
        path = tmp_path / "ui.json"
        card = PlanCard(on_activate=lambda _plan_id: None, with_heading=False)
        section = SummarySection("plan", "Plan", card, UiState(path))
        assert not section.folded
        section.header.emit("clicked")
        assert section.folded
        assert section.has_css_class("ac-folded")
        again = SummarySection("plan", "Plan", PlanCard(on_activate=lambda _plan_id: None), UiState(path))
        assert again.folded
        again.set_detail("2/8")
        assert again.detail_label.get_text() == "2/8"

    _in_gtk_context(check)


def test_a_person_accepts_or_sends_back_a_step_on_verification() -> None:
    base = _demo_plan()
    waiting = msgspec.structs.replace(base.nodes[0], status=NodeStatus.VERIFYING, evidence="tests <passed>")
    other = msgspec.structs.replace(base.nodes[1], status=NodeStatus.PENDING, depends_on=())
    plan = msgspec.structs.replace(base, nodes=(waiting, other))

    def check() -> None:
        decided: list[tuple[NodeId, bool]] = []
        graph = PlanGraph(plan, lambda: None, None, lambda node, accept: decided.append((node, accept)))
        assert not graph.accept_button.get_visible()
        graph.select_node(other.id)
        assert not graph.accept_button.get_visible()
        graph.select_node(waiting.id)
        assert graph.accept_button.get_visible()
        assert graph.send_back_button.get_visible()
        # A report is shown as text, whatever it contains.
        assert "tests <passed>" in graph.details_label.get_text()
        graph.accept_button.emit("clicked")
        assert decided == [(waiting.id, True)]
        assert not graph.accept_button.get_sensitive()
        graph.decision_failed()
        assert graph.accept_button.get_sensitive()
        graph.send_back_button.emit("clicked")
        assert decided[-1] == (waiting.id, False)

    _in_gtk_context(check)


def test_without_a_decision_handler_no_step_offers_to_be_accepted() -> None:
    base = _demo_plan()
    waiting = msgspec.structs.replace(base.nodes[0], status=NodeStatus.VERIFYING)
    plan = msgspec.structs.replace(base, nodes=(waiting,))

    def check() -> None:
        graph = PlanGraph(plan, lambda: None)
        graph.select_node(waiting.id)
        assert not graph.accept_button.get_visible()

    _in_gtk_context(check)


def test_heartbeat_keeps_plan_buttons_and_selection() -> None:
    def check() -> None:
        plan = _demo_plan()
        graph = PlanGraph(plan, lambda: None)
        node = plan.nodes[0]
        graph.select_node(node.id)
        buttons = dict(graph.node_buttons)
        changed = msgspec.structs.replace(node, evidence="latest evidence", priority=node.priority + 1)
        updated = msgspec.structs.replace(plan, nodes=(changed, *plan.nodes[1:]))
        assert graph.update(updated)
        assert graph.node_buttons == buttons
        assert graph.selected_node == node.id
        assert "latest evidence" in graph.details_label.get_text()
        topology = msgspec.structs.replace(changed, layer=changed.layer + 1)
        assert not graph.update(msgspec.structs.replace(updated, nodes=(topology, *plan.nodes[1:])))

    _in_gtk_context(check)


def _hdag_plan() -> PlanView:
    from aconsole.contracts.common import NodeId

    plan = _demo_plan()
    base = plan.nodes[0]
    core = msgspec.structs.replace(base, id=NodeId("core"), key="CORE-0", title="Конвейер", level=0, depends_on=())
    qa = msgspec.structs.replace(base, id=NodeId("qa"), key="QA-0", title="Проверки", level=0, depends_on=())
    modules = tuple(
        msgspec.structs.replace(
            base,
            id=NodeId(f"module-{i}"),
            key=f"{prefix}-{i}",
            title=title,
            level=1,
            parent=parent,
            pipeline=True,
            status=status,
            layer=i,
            depends_on=(),
            attempt_no=attempt,
            last_verdict=verdict,
        )
        for i, prefix, title, parent, status, attempt, verdict in (
            (
                1,
                "CORE",
                "Контракт модуля",
                core.id,
                NodeStatus.BLOCKED,
                2,
                "1 passed, 1 failed: acceptance needs review",
            ),
            (2, "CORE", "Передача контекста", core.id, NodeStatus.VERIFYING, 1, "2 passed, 0 failed"),
            (3, "QA", "Радиус проверки", qa.id, NodeStatus.IN_PROGRESS, 1, None),
            (4, "QA", "Квитанции", qa.id, NodeStatus.DONE, 1, "2 passed, 0 failed"),
        )
    )
    return msgspec.structs.replace(
        plan, title="AConsole · Детерминированный конвейер", nodes=(core, qa, *modules), done=1, total=6, phase="draft"
    )


def test_hdag_groups_freeze_and_inbox_actions() -> None:
    from aconsole.contracts.app import PlanInboxEntry

    plan = _hdag_plan()

    def check() -> None:
        frozen: list[PlanId] = []
        reviewed: list[tuple[str, str, str]] = []
        graph = PlanGraph(
            plan,
            lambda: None,
            on_freeze=frozen.append,
            on_review=lambda request, action, note: reviewed.append((request, action, note)),
        )
        assert set(graph.node_buttons) == {node.id for node in plan.nodes if node.level == 1}
        graph.freeze_button.emit("clicked")
        graph.freeze_button.emit("clicked")
        assert frozen == [plan.id]
        assert graph.update(msgspec.structs.replace(plan, phase="frozen"))
        assert not graph.freeze_button.get_visible()
        request = PlanInboxEntry(
            id="request-1",
            plan_id=plan.id,
            node_id=plan.nodes[2].id,
            kind="replan",
            body="Уточнить критерий приёмки",
            status="open",
            payload_json="{}",
            created_at=plan.updated_at,
        )
        graph.set_inbox((request,))
        assert len(graph.node_buttons) == 4
        assert graph.inbox_scroll.get_visible()
        row = graph.inbox_list.get_last_child()
        assert isinstance(row, Gtk.Box)
        first = row.get_first_child()
        assert first is not None
        note = first.get_next_sibling()
        assert isinstance(note, Gtk.Entry)
        actions = graph._review_actions[request.id]
        unblock = actions.get_first_child()
        assert isinstance(unblock, Gtk.Button)
        unblock.emit("clicked")
        assert reviewed == []
        note.set_text("Критерий уточнён")
        unblock.emit("clicked")
        unblock.emit("clicked")
        assert reviewed == [(request.id, "unblock", "Критерий уточнён")]
        graph.review_finished(request.id)
        assert actions.get_sensitive()
        assert note.get_text() == "Критерий уточнён"

    _in_gtk_context(check)


def test_freeze_button_calls_rpc_and_shows_frozen_phase(running_app: str) -> None:
    import asyncio

    failures: list[BaseException] = []

    class _FreezeApp(AConsoleApp):
        @override
        def do_activate(self) -> None:
            super().do_activate()
            _ = spawn_task(self._check_freeze())

        async def _check_freeze(self) -> None:
            async def check() -> None:
                while self.window is None:  # noqa: ASYNC110 -- GTK window creation has no awaitable notification
                    await asyncio.sleep(0.01)
                window = self.window
                plan = window._summary_vm.plan
                assert plan is not None
                window._open_plan(plan.id)
                page = window.plan_page
                assert page is not None
                assert page.freeze_button.get_visible()
                page.freeze_button.emit("clicked")
                while page.plan.phase != "frozen":  # noqa: ASYNC110 -- wait for the native widget RPC update
                    await asyncio.sleep(0.01)
                assert not page.freeze_button.get_visible()

            try:
                await asyncio.wait_for(check(), timeout=10)
            except BaseException as error:  # noqa: BLE001 -- retain GTK task failures for pytest
                failures.append(error)
            finally:
                await self._finish()

    app = _FreezeApp(CoreClient(running_app))
    assert run_app(lambda: app, ["aconsole-freeze-test"]) == 0
    assert failures == []
