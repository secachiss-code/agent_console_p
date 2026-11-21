"""Account quota windows in the shared card and the full limits dialog."""

from collections.abc import Awaitable, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, GObject, Gtk  # noqa: E402

from aconsole.contracts.app import AccountView, TabView, UsageOverviewView  # noqa: E402
from aconsole.contracts.common import AgentKind  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.views.quota_dialog import QuotaDialog  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402
from aconsole.ui.views.sidebar.quota_row import AccountLine  # noqa: E402


class QuotaCard(Gtk.Button):
    def __init__(self) -> None:
        super().__init__()
        self._all_hidden: bool = False
        self.add_css_class("flat")
        self.add_css_class("ac-summary-section")
        self._accounts: tuple[AccountView, ...] = ()
        self.tabs: tuple[TabView, ...] = ()
        self.account_lines: list[AccountLine] = []
        # Set by the window: asks the core for fresh limits. The answers come back through `set_accounts`.
        self.on_refresh: Callable[[], Awaitable[None]] | None = None
        self.read_usage: Callable[[], Awaitable[UsageOverviewView | None]] | None = None
        self._usage: UsageOverviewView | None = None
        self._snapshot_usage: UsageOverviewView | None = None
        self._hidden: frozenset[AgentKind] = frozenset()
        self._usage_request: int = 0
        self.details_dialog: QuotaDialog | None = None
        self._timer: int | None = None
        self._display: str = "used"
        self._thresholds: tuple[float, float] = (80, 95)
        self.rows: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
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

    def set_accounts(self, accounts: tuple[AccountView, ...], *, all_hidden: bool = False) -> None:
        if accounts == self._accounts and all_hidden == self._all_hidden and self.rows.get_first_child() is not None:
            self._sync_details()
            return
        self._all_hidden = all_hidden
        self._rebuild(accounts)
        if self.details_dialog is not None and self.details_dialog.get_visible():
            self._refresh_usage()

    def _rebuild(self, accounts: tuple[AccountView, ...]) -> None:
        self._accounts = accounts
        while (child := self.rows.get_first_child()) is not None:
            self.rows.remove(child)
        title = self.title()
        self.update_property([Gtk.AccessibleProperty.LABEL], [GObject.Value(GObject.TYPE_STRING, title)])
        heading = Gtk.Label(label=title.upper(), xalign=0)
        heading.add_css_class("caption-heading")
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
            key = "quota.all_hidden" if self._all_hidden else "quota.empty"
            empty = Gtk.Label(label=translate(key), xalign=0, wrap=True)
            empty.add_css_class("ac-summary-detail")
            self.rows.append(empty)
        self._sync_details()

    @property
    def accounts(self) -> tuple[AccountView, ...]:
        return self._accounts

    @property
    def display(self) -> str:
        """ "used" or "remaining", as the settings chose."""
        return self._display

    def title(self) -> str:
        return translate("quota.title_left" if self._display == "remaining" else "quota.title")

    def _clicked(self, _button: Gtk.Button) -> None:
        root = self.get_root()
        if isinstance(root, Gtk.Widget):
            _ = self.open_details(root)

    def set_usage(self, usage: UsageOverviewView | None, hidden: frozenset[AgentKind]) -> None:
        if (usage, hidden) == (self._snapshot_usage, self._hidden):
            return
        if usage != self._snapshot_usage:
            self._snapshot_usage = self._usage = usage
        self._hidden = hidden
        self._show_usage()

    def _show_usage(self) -> None:
        dialog = self.details_dialog
        if dialog is None:
            return
        usage = self._usage
        if usage is None:
            dialog.update_usage(None)
            return
        histories = tuple(history for history in usage.histories if history.agent not in self._hidden)
        active_days = len({day.day for history in histories for day in history.days})
        dialog.update_usage(UsageOverviewView(histories=histories, active_days=active_days))

    def _refresh_usage(self) -> None:
        if self.read_usage is not None:
            self._usage_request += 1
            _ = spawn_task(self._load_usage(self.read_usage, self._usage_request))

    async def _load_usage(self, read: Callable[[], Awaitable[UsageOverviewView | None]], request: int) -> None:
        try:
            usage = await read()
        except Exception:  # noqa: BLE001 -- keep the last statistics on temporary connection failures
            return
        if request == self._usage_request:
            self._usage = usage
            self._show_usage()

    def open_details(self, parent: Gtk.Widget) -> QuotaDialog:
        if self.details_dialog is not None and self.details_dialog.get_visible():
            self.details_dialog.present(parent)
            return self.details_dialog
        dialog = QuotaDialog(
            self._accounts,
            self.tabs,
            self._display,
            on_refresh=self.on_refresh,
            thresholds=self._thresholds,
        )
        self.details_dialog = dialog
        self._show_usage()
        dialog.present(parent)
        self._refresh_usage()
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
