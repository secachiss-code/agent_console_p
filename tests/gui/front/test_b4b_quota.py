"""B4b combined plan and quota summary card behavior."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, override

import gi
import msgspec

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from aconsole.contracts.app import AccountView, QuotaWindowView  # noqa: E402
from aconsole.contracts.common import AgentKind, QuotaLevel  # noqa: E402
from aconsole.contracts.events import PlanUpdated, QuotaUpdated, TabUpdated  # noqa: E402
from aconsole.contracts.ipc import EventEnvelope  # noqa: E402
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabsViewModel  # noqa: E402
from aconsole.ui.views.window import MainWindow  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aconsole.ui.views.quota_dialog import QuotaDialog
    from aconsole.ui.views.sidebar.quota_card import QuotaCard


_NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
_TIMEOUT_SECONDS = 5.0


class _QuotaTestApp(Adw.Application):
    """Run GTK assertions on the supported application event loop."""

    def __init__(self, scenario: Callable[[_QuotaTestApp], Awaitable[None]]) -> None:
        super().__init__(
            application_id="io.github.aconsole.B4bQuotaTest",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.scenario = scenario
        self.client = CoreClient("unused")
        self.window: MainWindow | None = None
        self.error: Exception | None = None

    @override
    def do_activate(self) -> None:
        self.hold()
        _ = spawn_task(self._run_scenario())

    async def _run_scenario(self) -> None:
        try:
            await asyncio.wait_for(self.scenario(self), timeout=_TIMEOUT_SECONDS)
        except Exception as error:  # noqa: BLE001 -- report GTK scenario failures to pytest.
            self.error = error
        finally:
            if self.window is not None:
                self.window.close()
            self.release()
            self.quit()


def _run_ui(scenario: Callable[[_QuotaTestApp], Awaitable[None]]) -> None:
    app = _QuotaTestApp(scenario)
    assert run_app(lambda: app, ["aconsole-b4b-quota-test"]) == 0
    assert app.error is None, repr(app.error)


def _quota(
    key: Literal["5h", "7d", "week", "month", "budget"],
    used_pct: float | None,
    level: QuotaLevel,
    *,
    estimated: bool = False,
) -> QuotaWindowView:
    return QuotaWindowView(
        key=key,
        used_pct=used_pct,
        resets_at=_NOW.replace(hour=16, minute=14),
        level=level,
        observed_at=_NOW,
        estimated=estimated,
    )


def _quota_accounts() -> tuple[AccountView, ...]:
    snapshot = demo_snapshot()
    accounts: list[AccountView] = []
    for account in snapshot.accounts:
        if account.agent is AgentKind.CLAUDE:
            accounts.append(
                msgspec.structs.replace(
                    account,
                    quota=(
                        _quota("5h", 76, QuotaLevel.WARN),
                        _quota("7d", 31, QuotaLevel.OK),
                    ),
                )
            )
        else:
            accounts.append(
                msgspec.structs.replace(
                    account,
                    quota=(
                        _quota("5h", None, QuotaLevel.UNKNOWN),
                        _quota("7d", 97, QuotaLevel.CRITICAL, estimated=True),
                    ),
                )
            )
    return tuple(accounts)


def _labels(widget: Gtk.Widget) -> list[str]:
    texts = [widget.get_text()] if isinstance(widget, Gtk.Label) else []
    child = widget.get_first_child()
    while child is not None:
        texts.extend(_labels(child))
        child = child.get_next_sibling()
    return texts


def _assert_quota_card_rows(card: QuotaCard) -> None:
    # One line per account, with one bar: the window closest to its limit, and the time until it resets.
    labels = [line.percent_label.get_text() for line in card.account_lines]
    assert any("76" in label for label in labels)
    assert any("97" in label and "≈" in label for label in labels)
    assert all(line.time_label.get_text() for line in card.account_lines if line.peak is not None)
    for line in card.account_lines:
        assert line.name_label.get_text() not in line.time_label.get_text()
        assert "5 ч" not in line.time_label.get_text()
        assert "7 д" not in line.time_label.get_text()

    normal_line = next(line for line in card.account_lines if line.peak is not None and line.peak.used_pct == 76)
    assert normal_line.level_bar.get_min_value() == 0
    assert normal_line.level_bar.get_max_value() == 1
    assert normal_line.level_bar.get_value() == 0.76
    has_low, low = normal_line.level_bar.get_offset_value("low")
    has_high, high = normal_line.level_bar.get_offset_value("high")
    assert has_low
    assert low == 0.8
    assert has_high
    assert high == 0.95


def _assert_quota_dialog(dialog: QuotaDialog) -> None:
    assert dialog.get_visible()
    child = dialog.get_child()
    assert child is not None
    dialog_labels = _labels(child)
    assert any("76" in label for label in dialog_labels)
    assert any("97" in label and "≈" in label for label in dialog_labels)
    assert any(
        any(value in label for value in ("5h", "5 h", "5 ч"))
        for label in dialog_labels
    )
    assert any(
        any(value in label for value in ("7d", "7 d", "7 д"))
        for label in dialog_labels
    )
    dialog_unknown = next(
        row for row in dialog.window_rows if row.window.used_pct is None
    )
    assert not dialog_unknown.level_bar.get_visible()
    assert dialog_unknown.get_tooltip_text()
    before_times = [row.time_label.get_text() for row in dialog.window_rows]
    dialog.refresh_times()
    assert [row.time_label.get_text() for row in dialog.window_rows] == before_times


def _assert_summary_container(window: MainWindow) -> None:
    summary = window.summary_card
    assert isinstance(summary, Gtk.Box)
    assert summary.get_visible()
    sections = (window.plan_section, window.quota_section, window.git_section)
    assert [section.body for section in sections] == [window.plan_card, window.quota_card, window.git_card]
    children: list[Gtk.Widget] = []
    child = summary.get_first_child()
    while child is not None:
        children.append(child)
        child = child.get_next_sibling()
    assert children[0::2] == list(sections)
    assert all(isinstance(item, Gtk.Separator) for item in children[1::2])


def test_b4b_quota_card_shows_normal_unknown_estimated_and_critical_windows() -> None:
    from aconsole.ui.views.quota_dialog import QuotaDialog
    from aconsole.ui.views.sidebar.quota_card import QuotaCard

    async def scenario(app: _QuotaTestApp) -> None:
        window = MainWindow(app, TabsViewModel(app.client))
        app.window = window
        window.present()
        card = QuotaCard()
        card.set_accounts(_quota_accounts())
        card.set_visible(True)
        await asyncio.sleep(0.05)

        assert card.get_visible()
        _assert_quota_card_rows(card)

        dialog = card.open_details(window)
        assert isinstance(dialog, QuotaDialog)
        assert isinstance(dialog, Adw.Dialog)
        _assert_quota_dialog(dialog)
        dialog.close()

    _run_ui(scenario)


def test_b4b_summary_card_combines_plan_and_quota_and_tracks_live_updates() -> None:
    from aconsole.ui.views.quota_dialog import QuotaDialog

    async def scenario(app: _QuotaTestApp) -> None:
        snapshot = demo_snapshot()
        accounts = _quota_accounts()
        snapshot = msgspec.structs.replace(snapshot, accounts=accounts)
        app.client.store.apply_snapshot(snapshot, 0)
        window = MainWindow(app, TabsViewModel(app.client))
        app.window = window
        window.present()
        await asyncio.sleep(0.05)

        _assert_summary_container(window)

        plan = next(
            item
            for item in snapshot.plans
            if item.project_id == snapshot.active_project and item.status == "active"
        )
        assert window.plan_card.plan_id == plan.id
        assert window.plan_card.title_label.get_text() == plan.title
        assert any("76" in line.percent_label.get_text() for line in window.quota_card.account_lines)

        dialog = window.quota_card.open_details(window)
        assert isinstance(dialog, QuotaDialog)
        assert dialog.get_visible()
        assert window.quota_card.open_details(window) is dialog
        window.quota_card.emit("clicked")
        assert window.quota_card.details_dialog is dialog

        changed_plan = msgspec.structs.replace(
            plan, title="Live Plan Update", done=plan.done + 1, updated_at=_NOW
        )
        account = next(item for item in accounts if item.agent is AgentKind.CLAUDE)
        changed_account = msgspec.structs.replace(
            account,
            plan_label="Team",
            quota=(_quota("5h", 88, QuotaLevel.CRITICAL), _quota("7d", 44, QuotaLevel.WARN)),
        )
        assert app.client.store.apply(
            EventEnvelope(seq=1, at=_NOW, event=PlanUpdated(plan=changed_plan))
        )
        assert app.client.store.apply(
            EventEnvelope(seq=2, at=_NOW, event=QuotaUpdated(account=changed_account))
        )
        tab = next(item for item in snapshot.tabs if item.account_id == account.id)
        changed_tab = msgspec.structs.replace(tab, title="Live Tab Update")
        assert app.client.store.apply(
            EventEnvelope(
                seq=3,
                at=_NOW,
                event=TabUpdated(tab=changed_tab, changed=frozenset({"title"})),
            )
        )
        await asyncio.sleep(0.05)

        assert window.plan_card.title_label.get_text() == "Live Plan Update"
        assert str(changed_plan.done) in window.plan_card.progress_label.get_text()
        assert any("88" in line.percent_label.get_text() for line in window.quota_card.account_lines)
        assert any("88" in row.percent_label.get_text() for row in dialog.window_rows)
        # The list stays short; a card opens its account's details on the right, with the tabs using it.
        assert "Live Tab Update" not in " ".join(_labels(dialog.content))
        dialog.open_account(account.id)
        assert dialog.panel_revealer.get_reveal_child()
        assert dialog.panel.title_label.get_text() == "Claude"
        panel_labels = " ".join(_labels(dialog.panel))
        assert "Live Tab Update" in panel_labels
        assert "Team" in panel_labels
        assert "на " not in " ".join(_labels(dialog.content))
        dialog.account_cards[account.id].emit("clicked")
        assert not dialog.panel_revealer.get_reveal_child()
        dialog.close()

    _run_ui(scenario)
