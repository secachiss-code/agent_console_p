"""Account quota windows in the shared card and the full limits dialog."""

import locale
from datetime import UTC, datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from aconsole.contracts.app import AccountView, QuotaWindowView, TabView  # noqa: E402
from aconsole.ui.viewmodels.logic.quota_fmt import format_percent, quota_level  # noqa: E402
from aconsole.ui.viewmodels.logic.time_fmt import format_remaining  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402


class QuotaRow(Gtk.Box):
    def __init__(self, window: QuotaWindowView) -> None:
        super().__init__(spacing=6)
        self.window: QuotaWindowView = window
        self.add_css_class(f"ac-quota-{quota_level(window.used_pct)}")
        self.key_label: Gtk.Label = Gtk.Label(label=translate(f"quota.{window.key}"), xalign=0)
        self.key_label.set_width_chars(3)
        self.append(self.key_label)
        self.level_bar: Gtk.LevelBar = Gtk.LevelBar(min_value=0, max_value=100, hexpand=True)
        self.level_bar.set_value(window.used_pct if window.used_pct is not None else 0)
        self.level_bar.add_offset_value("low", 80)
        self.level_bar.add_offset_value("high", 95)
        self.level_bar.set_visible(window.used_pct is not None)
        if window.used_pct is None:
            self.set_tooltip_text(translate("quota.unknown.copy"))
        self.level_bar.set_valign(Gtk.Align.CENTER)
        self.append(self.level_bar)
        self.percent_label: Gtk.Label = Gtk.Label(label=format_percent(window.used_pct, estimated=window.estimated))
        self.percent_label.add_css_class("numeric")
        self.percent_label.set_width_chars(5)
        self.append(self.percent_label)
        self.time_label: Gtk.Label = Gtk.Label(xalign=1)
        self.time_label.add_css_class("numeric")
        self.time_label.set_width_chars(4)
        self.append(self.time_label)
        self.refresh_time()

    def refresh_time(self) -> None:
        reset = self.window.resets_at
        language = locale.getlocale()[0] or "en"
        self.time_label.set_label(
            format_remaining(int((reset - datetime.now(UTC)).total_seconds()), language) if reset else "—",
        )
        self.time_label.set_tooltip_text(
            translate("quota.reset").format(time=reset.astimezone().strftime("%H:%M")) if reset else None,
        )


class QuotaCard(Gtk.Button):
    def __init__(self) -> None:
        super().__init__()
        self.add_css_class("flat")
        self.add_css_class("ac-summary-section")
        self._accounts: tuple[AccountView, ...] = ()
        self.tabs: tuple[TabView, ...] = ()
        self.window_rows: list[QuotaRow] = []
        self.details_dialog: Adw.Dialog | None = None
        self._timer: int | None = None
        self.rows: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.summary_label: Gtk.Label = Gtk.Label(xalign=0)
        self.summary_label.add_css_class("ac-summary-detail")
        self.set_child(self.rows)
        _ = self.connect("clicked", self._clicked)
        _ = self.connect("map", self._mapped)
        _ = self.connect("unmap", self._unmapped)
        self.set_accounts(())

    def set_accounts(self, accounts: tuple[AccountView, ...]) -> None:
        if accounts == self._accounts and self.rows.get_first_child() is not None:
            return
        self._accounts = accounts
        while (child := self.rows.get_first_child()) is not None:
            self.rows.remove(child)
        self.window_rows.clear()
        title = Gtk.Label(label=translate("quota.title").upper(), xalign=0)
        title.add_css_class("caption-heading")
        self.rows.append(title)
        for account in accounts:
            label = Gtk.Label(label=self._account_title(account), xalign=0)
            label.add_css_class("heading")
            self.rows.append(label)
            for window in account.quota:
                row = QuotaRow(window)
                self.window_rows.append(row)
                self.rows.append(row)
            if not account.quota:
                self.rows.append(Gtk.Label(label=translate("quota.unknown"), xalign=0))
        observations = [window.observed_at for account in accounts for window in account.quota if window.observed_at]
        self.summary_label.set_label(
            translate("quota.observed").format(time=min(observations).astimezone().strftime("%H:%M"))
            if observations else translate("quota.unknown"),
        )
        self.rows.append(self.summary_label)

    @staticmethod
    def _account_title(account: AccountView) -> str:
        return " · ".join(value for value in (account.agent.value.capitalize(), account.plan_label) if value)

    def _clicked(self, _button: Gtk.Button) -> None:
        root = self.get_root()
        if isinstance(root, Gtk.Widget):
            _ = self.open_details(root)

    def open_details(self, parent: Gtk.Widget) -> Adw.Dialog:
        dialog = Adw.Dialog(title=translate("quota.title"), content_width=520, content_height=520)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.add_css_class("ac-quota-details")
        for account in self._accounts:
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            heading = Gtk.Label(label=self._account_title(account), xalign=0)
            heading.add_css_class("heading")
            group.append(heading)
            for window in account.quota:
                group.append(QuotaRow(window))
            if not account.quota:
                group.append(Gtk.Label(label=translate("quota.unknown.copy"), xalign=0, wrap=True))
            titles = ", ".join(tab.title for tab in self.tabs if tab.account_id == account.id)
            if titles:
                group.append(Gtk.Label(label=f"{translate('quota.tabs')}: {titles}", xalign=0, wrap=True))
            content.append(group)
        footer = Gtk.Label(label=self.summary_label.get_label(), xalign=0, wrap=True)
        footer.add_css_class("ac-summary-detail")
        content.append(footer)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(content)
        toolbar.set_content(scroll)
        dialog.set_child(toolbar)
        self.details_dialog = dialog
        dialog.present(parent)
        return dialog

    def _mapped(self, _widget: Gtk.Widget) -> None:
        if self._timer is None:
            self._timer = GLib.timeout_add_seconds(30, self._tick)

    def _unmapped(self, _widget: Gtk.Widget) -> None:
        if self._timer is not None:
            _ = GLib.source_remove(self._timer)
            self._timer = None

    def _tick(self) -> bool:
        for row in self.window_rows:
            row.refresh_time()
        return GLib.SOURCE_CONTINUE
