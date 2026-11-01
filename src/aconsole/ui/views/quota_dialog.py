"""Live account limits, reset times and the tabs using each account."""

from datetime import UTC, datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from aconsole.contracts.app import AccountView, TabView  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402
from aconsole.ui.views.sidebar.quota_row import QuotaRow  # noqa: E402


class QuotaDialog(Adw.Dialog):
    def __init__(self, accounts: tuple[AccountView, ...], tabs: tuple[TabView, ...], summary: str) -> None:
        super().__init__(title=translate("quota.title"), content_width=520, content_height=520)
        self._accounts: tuple[AccountView, ...] = ()
        self._tab_signature: tuple[tuple[str, str | None, str], ...] = ()
        self._summary: str = ""
        self._timer: int | None = None
        self.window_rows: list[QuotaRow] = []
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.content: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.content.add_css_class("ac-quota-details")
        self.scroll: Gtk.ScrolledWindow = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroll.set_child(self.content)
        toolbar.set_content(self.scroll)
        self.set_child(toolbar)
        _ = self.connect("map", self._mapped)
        _ = self.connect("unmap", self._unmapped)
        self.update_accounts(accounts, tabs, summary)

    def update_accounts(self, accounts: tuple[AccountView, ...], tabs: tuple[TabView, ...], summary: str) -> None:
        signature = tuple(
            (str(tab.id), str(tab.account_id) if tab.account_id is not None else None, tab.title) for tab in tabs
        )
        if (
            (accounts, signature, summary) == (self._accounts, self._tab_signature, self._summary)
            and self.content.get_first_child() is not None
        ):
            return
        self._accounts, self._tab_signature, self._summary = accounts, signature, summary
        while (child := self.content.get_first_child()) is not None:
            self.content.remove(child)
        self.window_rows.clear()
        for account in accounts:
            self.content.append(self._account_group(account, tabs))
        if not accounts:
            self.content.append(Gtk.Label(label=translate("quota.unknown.copy"), xalign=0, wrap=True))
        footer = Gtk.Label(label=f"{summary}\n{translate('quota.source_unknown')}", xalign=0, wrap=True)
        footer.add_css_class("ac-summary-detail")
        self.content.append(footer)

    def _account_group(self, account: AccountView, tabs: tuple[TabView, ...]) -> Gtk.Box:
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        title = " · ".join(value for value in (account.agent.value.capitalize(), account.plan_label) if value)
        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class("heading")
        group.append(heading)
        for window in account.quota:
            row = QuotaRow(window)
            self.window_rows.append(row)
            group.append(row)
            if window.resets_at is not None:
                reset = window.resets_at.astimezone()
                pattern = "%H:%M" if reset.date() == datetime.now(UTC).astimezone().date() else "%d.%m %H:%M"
                group.append(Gtk.Label(label=translate("quota.reset").format(time=reset.strftime(pattern)), xalign=0))
            if window.used_pct is None:
                group.append(Gtk.Label(label=translate("quota.unknown.copy"), xalign=0, wrap=True))
        if not account.quota:
            group.append(Gtk.Label(label=translate("quota.unknown.copy"), xalign=0, wrap=True))
        titles = ", ".join(tab.title for tab in tabs if tab.account_id == account.id)
        if titles:
            group.append(Gtk.Label(label=f"{translate('quota.tabs')}: {titles}", xalign=0, wrap=True))
        return group

    def refresh_times(self) -> None:
        for row in self.window_rows:
            row.refresh_time()

    def _mapped(self, _widget: Gtk.Widget) -> None:
        self.refresh_times()
        if self._timer is None:
            self._timer = GLib.timeout_add_seconds(30, self._tick)

    def _unmapped(self, _widget: Gtk.Widget) -> None:
        if self._timer is not None:
            _ = GLib.source_remove(self._timer)
            self._timer = None

    def _tick(self) -> bool:
        self.refresh_times()
        return GLib.SOURCE_CONTINUE
