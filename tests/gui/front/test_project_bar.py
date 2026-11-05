"""The project switcher and the session search over the tab list."""

from __future__ import annotations

from datetime import UTC, datetime

import msgspec
import pytest

from aconsole.contracts.app import AccountView, QuotaWindowView
from aconsole.contracts.common import AccountId, AgentKind, QuotaLevel
from aconsole.contracts.events import TabUpdated
from aconsole.contracts.ipc import EventEnvelope
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.viewmodels.logic.quota_fmt import shown_percent
from aconsole.ui.viewmodels.tabs_vm import TabsViewModel
from aconsole.ui.views.sidebar.quota_card import QuotaCard
from aconsole.ui.views.tabs.project_bar import ProjectBar
from aconsole.ui.views.tabs.sidebar import TabSidebar


def _account() -> AccountView:
    window = QuotaWindowView(
        key="5h", used_pct=30.0, resets_at=datetime(2030, 1, 1, tzinfo=UTC), level=QuotaLevel.OK,
        observed_at=datetime(2029, 1, 1, tzinfo=UTC), estimated=False,
    )
    return AccountView(id=AccountId("a"), agent=AgentKind.CODEX, plan_label="Plus", quota=(window,))


def test_limits_show_the_part_used_or_the_part_left() -> None:
    assert shown_percent(30.0, "used") == 30.0
    assert shown_percent(30.0, "remaining") == 70.0
    assert shown_percent(None, "remaining") is None
    card = QuotaCard()
    card.set_accounts((_account(),))
    assert "30" in card.account_lines[0].windows[0].percent_label.get_text()
    card.set_display("remaining")
    assert "70" in card.account_lines[0].windows[0].percent_label.get_text()


def test_switcher_and_search_filter_the_tab_list() -> None:
    client = CoreClient("/nonexistent")
    client.store.apply_snapshot(demo_snapshot(), 1)
    snapshot = client.store.snapshot
    assert snapshot is not None
    tabs = TabsViewModel(client)
    sidebar = TabSidebar(tabs)
    bar = ProjectBar(tabs, sidebar)
    in_active = [tab for tab in snapshot.tabs if tab.project_id == snapshot.active_project]
    assert bar.projects.get_visible()
    assert sidebar.selection.get_n_items() == len(in_active)
    sidebar.set_project(None)
    assert sidebar.selection.get_n_items() == len(snapshot.tabs)
    sidebar.set_query(snapshot.tabs[0].title.upper())
    assert sidebar.selection.get_n_items() >= 1
    sidebar.set_query("no such session anywhere")
    assert sidebar.selection.get_n_items() == 0
    assert sidebar.get_visible_child_name() == "empty"
    sidebar.set_query("")
    assert sidebar.get_visible_child_name() == "tabs"


@pytest.mark.parametrize("field", ["title", "last_message", "branch"])
def test_search_follows_live_tab_changes(field: str) -> None:
    client = CoreClient("unused")
    snapshot = demo_snapshot()
    client.store.apply_snapshot(snapshot, 0)
    sidebar = TabSidebar(TabsViewModel(client))
    sidebar.set_query("unique-search-regression")
    assert sidebar.selection.get_n_items() == 0
    updated = msgspec.structs.replace(snapshot.tabs[0], **{field: "unique-search-regression"})
    client.store.apply(EventEnvelope(seq=1, at=datetime.now(UTC), event=TabUpdated(
        tab=updated, changed=frozenset({field}),
    )))
    assert sidebar.selection.get_n_items() == 1
    assert sidebar.get_visible_child_name() == "tabs"
    client.store.apply(EventEnvelope(seq=2, at=datetime.now(UTC), event=TabUpdated(
        tab=snapshot.tabs[0], changed=frozenset({field}),
    )))
    assert sidebar.selection.get_n_items() == 0
    assert sidebar.get_visible_child_name() == "empty"
