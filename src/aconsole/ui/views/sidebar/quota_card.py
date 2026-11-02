"""Account quota windows in the shared card and the full limits dialog."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk  # noqa: E402

from aconsole.contracts.app import AccountView, TabView  # noqa: E402
from aconsole.ui.views.quota_dialog import QuotaDialog  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402
from aconsole.ui.views.sidebar.quota_row import QuotaRow  # noqa: E402


class QuotaCard(Gtk.Button):
    def __init__(self) -> None:
        super().__init__()
        self.add_css_class("flat")
        self.add_css_class("ac-summary-section")
        self._accounts: tuple[AccountView, ...] = ()
        self.tabs: tuple[TabView, ...] = ()
        self.window_rows: list[QuotaRow] = []
        self.details_dialog: QuotaDialog | None = None
        self._timer: int | None = None
        self.rows: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.summary_label: Gtk.Label = Gtk.Label(xalign=1)
        self.summary_label.add_css_class("ac-summary-detail")
        self.set_child(self.rows)
        _ = self.connect("clicked", self._clicked)
        _ = self.connect("map", self._mapped)
        _ = self.connect("unmap", self._unmapped)
        self.set_accounts(())

    def set_accounts(self, accounts: tuple[AccountView, ...]) -> None:
        if accounts == self._accounts and self.rows.get_first_child() is not None:
            self._sync_details()
            return
        self._accounts = accounts
        parent = self.summary_label.get_parent()
        if isinstance(parent, Gtk.Box):
            parent.remove(self.summary_label)
        while (child := self.rows.get_first_child()) is not None:
            self.rows.remove(child)
        self.window_rows.clear()
        heading = Gtk.Box(spacing=8)
        title = Gtk.Label(label=translate("quota.title").upper(), xalign=0, hexpand=True)
        title.add_css_class("caption-heading")
        heading.append(title)
        heading.append(self.summary_label)
        self.rows.append(heading)
        grid = Gtk.Grid(column_spacing=16, row_spacing=10, column_homogeneous=True)
        for index, account in enumerate(accounts):
            column = self._account_column(account)
            grid.attach(column, index % 2, index // 2, 2 if len(accounts) == 1 else 1, 1)
        if accounts:
            self.rows.append(grid)
        else:
            empty = Gtk.Label(label=translate("quota.empty"), xalign=0, wrap=True)
            empty.add_css_class("ac-summary-detail")
            self.rows.append(empty)
        observations = [window.observed_at for account in accounts for window in account.quota if window.observed_at]
        self.summary_label.set_label(
            translate("quota.observed").format(time=min(observations).astimezone().strftime("%d.%m %H:%M"))
            if observations else "",
        )
        self._sync_details()

    def _account_column(self, account: AccountView) -> Gtk.Box:
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        name = Gtk.Label(label=account.agent.value.capitalize(), xalign=0)
        name.add_css_class("ac-summary-name")
        column.append(name)
        for window in account.quota:
            row = QuotaRow(window, compact=True)
            self.window_rows.append(row)
            column.append(row)
        if not account.quota:
            missing = Gtk.Label(label=translate("quota.unknown"), xalign=0, wrap=True)
            missing.add_css_class("ac-summary-detail")
            column.append(missing)
        return column

    def _clicked(self, _button: Gtk.Button) -> None:
        root = self.get_root()
        if isinstance(root, Gtk.Widget):
            _ = self.open_details(root)

    def open_details(self, parent: Gtk.Widget) -> QuotaDialog:
        if self.details_dialog is not None and self.details_dialog.get_visible():
            self.details_dialog.present(parent)
            return self.details_dialog
        dialog = QuotaDialog(self._accounts, self.tabs, self.summary_label.get_label())
        self.details_dialog = dialog
        dialog.present(parent)
        return dialog

    def _sync_details(self) -> None:
        if self.details_dialog is not None and self.details_dialog.get_visible():
            self.details_dialog.update_accounts(self._accounts, self.tabs, self.summary_label.get_label())

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
