"""Non-colour status cues and keyboard sidebar controls."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import gi
import msgspec

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from aconsole.contracts.app import EmptyResult, ReorderParams  # noqa: E402
from aconsole.contracts.common import NodeId, NodeStatus  # noqa: E402
from aconsole.contracts.events import TabUpdated  # noqa: E402
from aconsole.contracts.ipc import EventEnvelope  # noqa: E402
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as plan_text  # noqa: E402
from aconsole.ui.views.sidebar.plan_graph import PlanGraph  # noqa: E402
from aconsole.ui.views.tabs.messages import _ as tab_text  # noqa: E402
from aconsole.ui.views.tabs.row import TabRow  # noqa: E402
from aconsole.ui.views.tabs.sidebar import TabSidebar  # noqa: E402
from tests.gui.front.test_b2_tabs import _model, _run_ui, _TabsTestApp  # noqa: E402
from tests.gui.front.test_b4a_plan import _in_gtk_context  # noqa: E402

if TYPE_CHECKING:
    import pytest


def test_every_plan_state_is_visible_and_updates_on_heartbeat() -> None:
    base = demo_snapshot().plans[0]
    nodes = tuple(
        msgspec.structs.replace(base.nodes[0], id=NodeId(f"state-{status.value}"), status=status, depends_on=())
        for status in NodeStatus
    )
    plan = msgspec.structs.replace(base, nodes=nodes)

    def check() -> None:
        graph = PlanGraph(plan, lambda: None)
        for node in nodes:
            button = graph.node_buttons[node.id]
            label = button.get_child()
            assert isinstance(label, Gtk.Label)
            assert plan_text(f"plan.status.{node.status.value}") in label.get_text()
            assert node.title in button.get_tooltip_text()
            assert Gtk.test_accessible_has_property(button, Gtk.AccessibleProperty.LABEL)
        changed = msgspec.structs.replace(nodes[0], status=NodeStatus.DONE)
        original = graph.node_buttons[changed.id]
        assert graph.update(msgspec.structs.replace(plan, nodes=(changed, *nodes[1:])))
        assert graph.node_buttons[changed.id] is original
        label = original.get_child()
        assert isinstance(label, Gtk.Label)
        assert plan_text("plan.status.done") in label.get_text()
        assert plan_text("plan.status.done") in original.get_tooltip_text()

    _in_gtk_context(check)


def test_tab_states_have_visible_text_when_colours_are_unavailable() -> None:
    async def scenario(_app: _TabsTestApp) -> None:
        _client, tabs = _model(demo_snapshot())
        row = TabRow(tabs)
        item = tabs.store.get(0)
        row.bind_item(item)
        assert row.menu_button.get_focusable()
        assert row.menu_button.get_icon_name() == "view-more-symbolic"
        try:
            for status in (
                "starting", "idle", "working", "waiting_approval", "waiting_input", "error", "exited", "unknown",
            ):
                item.set_property("status-code", status)
                assert row.subtitle_label.get_text().startswith(tab_text(f"tabs.status.{status}"))
                assert row.subtitle_label.get_tooltip_text() == row.subtitle_label.get_text()
        finally:
            row.unbind_item()

    _run_ui(scenario)


def test_keyboard_reorder_reaches_rpc_and_respects_section_and_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario(app: _TabsTestApp) -> None:
        snapshot = demo_snapshot()
        client, tabs = _model(snapshot)
        sidebar = TabSidebar(tabs)
        assert app.window is not None
        app.window.set_content(sidebar)
        app.window.present()
        visible = [sidebar._sorted.get_item(index) for index in range(sidebar._sorted.get_n_items())]
        pair_index = next(index for index in range(len(visible) - 1)
                          if visible[index].section_key == visible[index + 1].section_key)
        first, second = visible[pair_index:pair_index + 2]
        sidebar.selection.set_selected(pair_index)
        requests: list[ReorderParams] = []
        sequence = 0

        async def request(method: str, params: object, _result_type: type[object]) -> EmptyResult:
            nonlocal sequence
            assert method == "tab/reorder"
            assert isinstance(params, ReorderParams)
            requests.append(params)
            current = client.store.snapshot
            assert current is not None
            tab = next(tab for tab in current.tabs if tab.id == params.tab_id)
            updated = msgspec.structs.replace(tab, order=params.order)
            sequence += 1
            assert client.store.apply(EventEnvelope(seq=sequence, at=tab.status_since,
                                      event=TabUpdated(tab=updated, changed=frozenset({"order"}))))
            return EmptyResult()

        monkeypatch.setattr(client, "request", request)
        assert not sidebar.reorder_keys.emit("key-pressed", Gdk.KEY_Down, 0, Gdk.ModifierType(0))
        assert sidebar.reorder_keys.emit("key-pressed", Gdk.KEY_Down, 0, Gdk.ModifierType.ALT_MASK)
        assert not sidebar.move_tab(str(first.tab_id), 1)  # pending operation is not duplicated
        assert sidebar.pending_reorder is not None
        await asyncio.wait_for(sidebar.pending_reorder, timeout=2)
        assert requests
        ordered = [tabs.store.get(index).tab_id for index in range(tabs.store.get_n_items())]
        assert ordered.index(second.tab_id) < ordered.index(first.tab_id)
        refreshed = [sidebar._sorted.get_item(index) for index in range(sidebar._sorted.get_n_items())]
        boundary = next(index for index in range(len(refreshed) - 1)
                        if refreshed[index].section_key != refreshed[index + 1].section_key)
        assert not sidebar.move_tab(str(refreshed[boundary].tab_id), 1)
        assert not sidebar.move_tab(str(refreshed[0].tab_id), -1)
        assert not sidebar.move_tab(str(refreshed[-1].tab_id), 1)
        # The same commands are reachable from the row's keyboard-openable menu.
        row = TabRow(tabs)
        row.bind_item(first)
        moves: list[tuple[str, int]] = []
        row.on_move = lambda tab, direction: moves.append((tab, direction)) or True
        assert row.activate_action("tab-row.move-up", None)
        assert moves == [(str(first.tab_id), -1)]
        row.unbind_item()
        assert row.activate_action("tab-row.move-down", None)
        assert len(moves) == 1  # recycled row has no target

    _run_ui(scenario)


def test_sidebar_controls_have_accessible_names() -> None:
    async def scenario(_app: _TabsTestApp) -> None:
        from aconsole.ui.views.sidebar.git_card import GitCard
        from aconsole.ui.views.sidebar.plan_card import PlanCard
        from aconsole.ui.views.sidebar.quota_card import QuotaCard
        from aconsole.ui.views.tabs.project_bar import ProjectBar

        _client, tabs = _model(demo_snapshot())
        sidebar = TabSidebar(tabs)
        bar = ProjectBar(tabs, sidebar)
        row = TabRow(tabs)
        card = PlanCard(lambda _plan: None)
        card.set_plan(demo_snapshot().plans[0])
        for control in (bar.projects, bar.search, bar.remove_button, row.close_button, row.menu_button,
                        row.title_label, sidebar.tab_list, card, GitCard(), QuotaCard()):
            assert Gtk.test_accessible_has_property(control, Gtk.AccessibleProperty.LABEL)

    _run_ui(scenario)
