"""Account quota windows in the shared card and the full limits dialog."""

from collections.abc import Awaitable, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, GObject, Gtk  # noqa: E402

from aconsole.contracts.app import AccountView, TabView  # noqa: E402
from aconsole.ui.i18n import format_date  # noqa: E402
from aconsole.ui.views.quota_dialog import QuotaDialog  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402
from aconsole.ui.views.sidebar.quota_row import AccountLine  # noqa: E402


class QuotaCard(Gtk.Button):
    def __init__(self, *, with_heading: bool = True) -> None:
        """Under a `SummarySection` the section names the card, and the time of the numbers goes last."""
        super().__init__()
        self.update_property(
            [Gtk.AccessibleProperty.LABEL], [GObject.Value(GObject.TYPE_STRING, translate("quota.title"))],
        )
        self._with_heading: bool = with_heading
        self.add_css_class("flat")
        self.add_css_class("ac-summary-section")
        self._accounts: tuple[AccountView, ...] = ()
        self.tabs: tuple[TabView, ...] = ()
        self.account_lines: list[AccountLine] = []
        # Set by the window: asks the core for fresh limits. The answers come back through `set_accounts`.
        self.on_refresh: Callable[[], Awaitable[None]] | None = None
        self.details_dialog: QuotaDialog | None = None
        self._timer: int | None = None
        self._display: str = "used"
        self._thresholds: tuple[float, float] = (80, 95)
        self.rows: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.summary_label: Gtk.Label = Gtk.Label(xalign=1)
        self.summary_label.add_css_class("ac-summary-detail")
        self.set_child(self.rows)
        _ = self.connect("clicked", self._clicked)
        _ = self.connect("map", self._mapped)
        _ = self.connect("unmap", self._unmapped)
        self.set_accounts(())

    def set_display(self, display: str) -> None:
        """Show the part used or the part left. Rebuilds the rows when the choice changed."""
        if display == self._display:
            return
        self._display = display
        self._rebuild(self._accounts)

    def set_thresholds(self, warn: float, critical: float) -> None:
        if (warn, critical) != self._thresholds:
            self._thresholds = (warn, critical)
            self._rebuild(self._accounts)

    def set_accounts(self, accounts: tuple[AccountView, ...]) -> None:
        if accounts == self._accounts and self.rows.get_first_child() is not None:
            self._sync_details()
            return
        self._rebuild(accounts)

    def _rebuild(self, accounts: tuple[AccountView, ...]) -> None:
        self._accounts = accounts
        parent = self.summary_label.get_parent()
        if isinstance(parent, Gtk.Box):
            parent.remove(self.summary_label)
        while (child := self.rows.get_first_child()) is not None:
            self.rows.remove(child)
        if self._with_heading:
            heading = Gtk.Box(spacing=8)
            title = Gtk.Label(label=self._title().upper(), xalign=0, hexpand=True)
            title.add_css_class("caption-heading")
            heading.append(title)
            heading.append(self.summary_label)
            self.rows.append(heading)
        self.account_lines.clear()
        if accounts:
            # A line each, whatever their number: one bar and the time to its reset. The details are one click away.
            lines = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            for account in accounts:
                line = AccountLine(account, display=self._display, thresholds=self._thresholds)
                self.account_lines.append(line)
                lines.append(line)
            self.rows.append(lines)
        else:
            empty = Gtk.Label(label=translate("quota.empty"), xalign=0, wrap=True)
            empty.add_css_class("ac-summary-detail")
            self.rows.append(empty)
        if not self._with_heading:
            self.summary_label.set_xalign(0)
            self.rows.append(self.summary_label)
        observations = [window.observed_at for account in accounts for window in account.quota if window.observed_at]
        self.summary_label.set_label(
            translate("quota.observed").format(time=format_date(min(observations)))
            if observations else "",
        )
        self.summary_label.set_visible(self._with_heading or bool(self.summary_label.get_label()))
        self._sync_details()

    @property
    def accounts(self) -> tuple[AccountView, ...]:
        return self._accounts

    @property
    def display(self) -> str:
        """"used" or "remaining", as the settings chose."""
        return self._display

    def title(self) -> str:
        """The card's name for the section header: limits used, or limits left."""
        return self._title()

    def _title(self) -> str:
        return translate("quota.title_left" if self._display == "remaining" else "quota.title")

    def _clicked(self, _button: Gtk.Button) -> None:
        root = self.get_root()
        if isinstance(root, Gtk.Widget):
            _ = self.open_details(root)

    def open_details(self, parent: Gtk.Widget) -> QuotaDialog:
        if self.details_dialog is not None and self.details_dialog.get_visible():
            self.details_dialog.present(parent)
            return self.details_dialog
        dialog = QuotaDialog(
            self._accounts, self.tabs, self._display, on_refresh=self.on_refresh, thresholds=self._thresholds,
        )
        self.details_dialog = dialog
        dialog.present(parent)
        return dialog

    def _sync_details(self) -> None:
        if self.details_dialog is not None and self.details_dialog.get_visible():
            self.details_dialog.set_display(self._display)
            self.details_dialog.set_thresholds(self._thresholds)
            self.details_dialog.update_accounts(self._accounts, self.tabs)

    def _mapped(self, _widget: Gtk.Widget) -> None:
        if self._timer is None:
            self._timer = GLib.timeout_add_seconds(30, self._tick)

    def _unmapped(self, _widget: Gtk.Widget) -> None:
        if self._timer is not None:
            _ = GLib.source_remove(self._timer)
            self._timer = None

    def _tick(self) -> bool:
        for line in self.account_lines:
            line.refresh_time()
        return GLib.SOURCE_CONTINUE
