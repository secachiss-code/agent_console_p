"""Live account limits, reset times and the tabs using each account."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from aconsole.contracts.app import AccountView, TabView  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.viewmodels.logic.quota_fmt import agent_name, format_money  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402
from aconsole.ui.views.sidebar.quota_row import QuotaRow  # noqa: E402

_REFRESH_WAIT_S = 15
_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600
_SECONDS_PER_DAY = 86400
_SOURCES = ("claude-usage", "codex-app-server", "cursor-dashboard", "opencode-go")


def source_text(source: str | None) -> str:
    """Where an account's numbers come from, in words. An unknown code is shown as it is."""
    if source is None:
        return translate("quota.source.none")
    return translate(f"quota.source.{source}") if source in _SOURCES else source


def _ago(observed: datetime | None) -> str:
    """When the numbers were taken: how long ago while it is recent, the date once it is not."""
    if observed is None:
        return translate("quota.info.never")
    local = observed.astimezone()
    seconds = max(0, int((datetime.now(UTC) - observed).total_seconds()))
    if seconds < _SECONDS_PER_MINUTE:
        return translate("quota.info.now").format(time=local.strftime("%H:%M:%S"))
    if seconds < _SECONDS_PER_HOUR:
        return translate("quota.info.minutes").format(
            time=local.strftime("%H:%M"), count=seconds // _SECONDS_PER_MINUTE,
        )
    if seconds < _SECONDS_PER_DAY:
        return translate("quota.info.hours").format(time=local.strftime("%H:%M"), count=seconds // _SECONDS_PER_HOUR)
    return translate("quota.info.date").format(time=local.strftime("%d.%m %H:%M"))


def _detail(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, wrap=True)
    label.add_css_class("ac-summary-detail")
    return label


class QuotaDialog(Adw.Dialog):
    def __init__(
        self,
        accounts: tuple[AccountView, ...],
        tabs: tuple[TabView, ...],
        summary: str,
        display: str = "used",
        *,
        on_refresh: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        title = translate("quota.title_left" if display == "remaining" else "quota.title")
        super().__init__(title=title, content_width=460)
        self._display: str = display
        self._accounts: tuple[AccountView, ...] = ()
        self._tab_signature: tuple[tuple[str, str | None, str], ...] = ()
        self._summary: str = ""
        self._timer: int | None = None
        self.window_rows: list[QuotaRow] = []
        self._on_refresh: Callable[[], Awaitable[None]] | None = on_refresh
        self._refresh_timer: int | None = None
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self._build_header())
        self.content: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.content.add_css_class("ac-quota-details")
        self.scroll: Gtk.ScrolledWindow = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
        )
        self.scroll.set_child(self.content)
        toolbar.set_content(self.scroll)
        self.set_child(toolbar)
        _ = self.connect("map", self._mapped)
        _ = self.connect("unmap", self._unmapped)
        self.update_accounts(accounts, tabs, summary)

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()
        self.refresh_button: Gtk.Button = Gtk.Button(icon_name="view-refresh-symbolic")
        self.refresh_button.set_tooltip_text(translate("quota.refresh"))
        self.refresh_button.set_sensitive(self._on_refresh is not None)
        _ = self.refresh_button.connect("clicked", self._refresh_clicked)
        self.refresh_spinner: Gtk.Spinner = Gtk.Spinner(visible=False)
        header.pack_start(self.refresh_button)
        header.pack_start(self.refresh_spinner)
        self.info_box: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.info_box.add_css_class("ac-quota-info")
        popover = Gtk.Popover(child=self.info_box)
        self.info_button: Gtk.MenuButton = Gtk.MenuButton(icon_name="help-about-symbolic", popover=popover)
        self.info_button.set_tooltip_text(translate("quota.info"))
        header.pack_end(self.info_button)
        return header

    def _refresh_clicked(self, _button: Gtk.Button) -> None:
        if self._on_refresh is None or self.refreshing:
            return
        self.refresh_button.set_sensitive(False)
        self.refresh_spinner.set_visible(True)
        self.refresh_spinner.start()
        # The answers come as limit updates; a silent agent must not leave the button off for good.
        self._refresh_timer = GLib.timeout_add_seconds(_REFRESH_WAIT_S, self._refresh_expired)
        _ = spawn_task(self._ask_refresh(self._on_refresh))

    async def _ask_refresh(self, ask: Callable[[], Awaitable[None]]) -> None:
        try:
            await ask()
        except Exception:  # noqa: BLE001 -- the core may be gone; the button comes back and the numbers stay
            self._refresh_done()

    @property
    def refreshing(self) -> bool:
        return self._refresh_timer is not None

    def _refresh_expired(self) -> bool:
        self._refresh_timer = None
        self._refresh_done()
        return GLib.SOURCE_REMOVE

    def _refresh_done(self) -> None:
        if self._refresh_timer is not None:
            _ = GLib.source_remove(self._refresh_timer)
            self._refresh_timer = None
        self.refresh_spinner.stop()
        self.refresh_spinner.set_visible(False)
        self.refresh_button.set_sensitive(self._on_refresh is not None)

    def _fill_info(self, accounts: tuple[AccountView, ...]) -> None:
        """Where each agent's numbers come from, when they were taken, and how often the core asks."""
        while (child := self.info_box.get_first_child()) is not None:
            self.info_box.remove(child)
        title = Gtk.Label(label=translate("quota.info"), xalign=0)
        title.add_css_class("heading")
        self.info_box.append(title)
        for account in accounts:
            block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            name = Gtk.Label(label=agent_name(account.agent.value), xalign=0)
            name.add_css_class("ac-summary-name")
            block.append(name)
            block.append(_detail(source_text(account.source)))
            observed = [window.observed_at for window in account.quota if window.observed_at is not None]
            block.append(_detail(_ago(max(observed) if observed else None)))
            if account.refresh_s:
                every = max(1, account.refresh_s // _SECONDS_PER_MINUTE)
                block.append(_detail(translate("quota.info.every").format(minutes=every)))
            self.info_box.append(block)
        if not accounts:
            self.info_box.append(_detail(translate("quota.source.none")))
        note = _detail(translate("quota.info.note"))
        note.set_max_width_chars(42)
        self.info_box.append(note)

    def set_display(self, display: str) -> None:
        """Empty the content: the next `update_accounts` rebuilds it with the new choice."""
        if display != self._display:
            self._display = display
            self.set_title(translate("quota.title_left" if display == "remaining" else "quota.title"))
            while (child := self.content.get_first_child()) is not None:
                self.content.remove(child)

    def update_accounts(self, accounts: tuple[AccountView, ...], tabs: tuple[TabView, ...], summary: str) -> None:
        signature = tuple(
            (str(tab.id), str(tab.account_id) if tab.account_id is not None else None, tab.title) for tab in tabs
        )
        if (
            (accounts, signature, summary) == (self._accounts, self._tab_signature, self._summary)
            and self.content.get_first_child() is not None
        ):
            return
        if self.refreshing and accounts != self._accounts:
            self._refresh_done()
        self._accounts, self._tab_signature, self._summary = accounts, signature, summary
        self._fill_info(accounts)
        while (child := self.content.get_first_child()) is not None:
            self.content.remove(child)
        self.window_rows.clear()
        for account in accounts:
            self.content.append(self._account_group(account, tabs))
        if not accounts:
            self.content.append(Gtk.Label(label=translate("quota.unknown.copy"), xalign=0, wrap=True))
        if summary:
            self.content.append(_detail(summary))

    def _account_group(self, account: AccountView, tabs: tuple[TabView, ...]) -> Gtk.Box:
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        group.add_css_class("ac-quota-group")
        heading = Gtk.Box(spacing=8)
        name = Gtk.Label(label=agent_name(account.agent.value), xalign=0, hexpand=True)
        name.add_css_class("heading")
        heading.append(name)
        if account.plan_label:
            plan = Gtk.Label(label=account.plan_label)
            plan.add_css_class("ac-git-pill")
            plan.add_css_class("dim-label")
            heading.append(plan)
        group.append(heading)
        for window in account.quota:
            row = QuotaRow(window, display=self._display)
            self.window_rows.append(row)
            group.append(row)
            notes: list[str] = []
            money = format_money(window.spent_usd, window.limit_usd)
            if money:
                # Money lines up under the bar it belongs to, so every bar in the dialog keeps one width.
                notes.append(translate("quota.spent").format(money=money))
            if window.resets_at is not None:
                reset = window.resets_at.astimezone()
                pattern = "%H:%M" if reset.date() == datetime.now(UTC).astimezone().date() else "%d.%m %H:%M"
                notes.append(translate("quota.reset").format(time=reset.strftime(pattern)))
            if notes:
                group.append(_detail(" · ".join(notes)))
            if window.used_pct is None:
                group.append(_detail(translate("quota.unknown.copy")))
        if not account.quota:
            group.append(_detail(translate("quota.unknown.copy")))
        titles = ", ".join(tab.title for tab in tabs if tab.account_id == account.id)
        if titles:
            group.append(_detail(f"{translate('quota.tabs')}: {titles}"))
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
