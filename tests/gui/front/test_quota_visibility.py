"""Hiding a CLI applies to every limits surface."""

import msgspec

from aconsole.contracts.common import AgentKind
from aconsole.contracts.settings import Settings
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
from aconsole.ui.viewmodels.tabs_vm import TabsViewModel
from aconsole.ui.views.tabs.new_tab import NewTabDialog
from aconsole.ui.views.window import MainWindow
from tests.gui.front.test_b4b_quota import _quota_accounts, _QuotaTestApp, _run_ui  # pyright: ignore[reportPrivateUsage]


def test_hiding_limits_updates_an_open_dialog_and_keeps_new_session_options() -> None:
    async def scenario(app: _QuotaTestApp) -> None:
        accounts = _quota_accounts()
        snapshot = msgspec.structs.replace(demo_snapshot(), accounts=accounts)
        app.client.store.apply_snapshot(snapshot, 0)
        tabs = TabsViewModel(app.client)
        window = MainWindow(app, tabs)
        app.window = window
        window.present()
        dialog = window.quota_card.open_details(window)
        assert window.quota_banner.get_revealed()
        assert set(dialog.account_cards) == {account.id for account in accounts}

        expected_agents = NewTabDialog(tabs, lambda _tab: None, lambda _text: None).agent_row.get_model()
        assert expected_agents is not None
        initial_count = expected_agents.get_n_items()
        assert initial_count > 0
        hidden = Settings(rev=2, vault_path="", worktrees_dir="", quota_hidden_agents=frozenset(AgentKind))
        window.preferences_vm.accept(hidden)
        assert not window.quota_card.accounts
        assert window.quota_settings_button.get_visible()
        assert not dialog.account_cards
        assert not window.quota_banner.get_revealed()
        agents = NewTabDialog(tabs, lambda _tab: None, lambda _text: None).agent_row.get_model()
        assert agents is not None
        assert agents.get_n_items() == initial_count

        window.preferences_vm.accept(msgspec.structs.replace(hidden, rev=3, quota_hidden_agents=frozenset()))
        assert set(dialog.account_cards) == {account.id for account in accounts}
        assert window.quota_banner.get_revealed()
        assert not window.quota_settings_button.get_visible()
        _ = dialog.close()

        window.preferences_vm.accept(msgspec.structs.replace(hidden, rev=4))
        window.quota_settings_button.emit("clicked")
        preferences = window.preferences_dialog
        assert preferences is not None
        assert preferences.limits_page.get_child_visible()

    _run_ui(scenario)
