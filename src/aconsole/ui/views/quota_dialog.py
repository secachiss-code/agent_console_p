"""Live account limits, reset times and the tabs using each account."""

from collections.abc import Awaitable, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from aconsole.contracts.app import AccountView, TabView  # noqa: E402
from aconsole.contracts.common import AccountId  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.viewmodels.logic.quota_fmt import agent_name  # noqa: E402
from aconsole.ui.views.quota_account import AccountPanel, ago, detail, source_text  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402
from aconsole.ui.views.sidebar.quota_row import QuotaRow, no_numbers_copy  # noqa: E402

_REFRESH_WAIT_S = 15
_SECONDS_PER_MINUTE = 60
_WIDTH = 460
_WIDTH_WITH_PANEL = 900
# Below this a window has no room for both: the panel takes the dialog until it is closed.
_ROOM_FOR_BOTH = 720


class QuotaDialog(Adw.Dialog):
    def __init__(
        self,
        accounts: tuple[AccountView, ...],
        tabs: tuple[TabView, ...],
        display: str = "used",
        *,
        on_refresh: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        title = translate("quota.title_left" if display == "remaining" else "quota.title")
        super().__init__(title=title, content_width=_WIDTH)
        self._display: str = display
        self._accounts: tuple[AccountView, ...] = ()
        self._tab_signature: tuple[tuple[str, str | None, str, float | None, int | None], ...] = ()
        self._tabs: tuple[TabView, ...] = ()
        self.open_account_id: AccountId | None = None
        self.account_cards: dict[AccountId, Gtk.Button] = {}
        self._timer: int | None = None
        self.window_rows: list[QuotaRow] = []
        self._on_refresh: Callable[[], Awaitable[None]] | None = on_refresh
        self._refresh_timer: int | None = None
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self._build_header())
        self.content: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.content.add_css_class("ac-quota-details")
        self.scroll: Gtk.ScrolledWindow = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True, hexpand=True,
        )
        self.scroll.set_child(self.content)
        self.panel: AccountPanel = AccountPanel(self.close_account)
        panel_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True, width_request=400,
        )
        panel_scroll.set_child(self.panel)
        self.panel_revealer: Gtk.Revealer = Gtk.Revealer(
            child=panel_scroll, transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT, reveal_child=False,
            visible=False,
        )
        sides = Gtk.Box()
        sides.append(self.scroll)
        sides.append(self.panel_revealer)
        toolbar.set_content(sides)
        self.set_child(toolbar)
        _ = self.connect("map", self._mapped)
        _ = self.connect("unmap", self._unmapped)
        self.update_accounts(accounts, tabs)

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
            block.append(detail(source_text(account.source)))
            observed = [window.observed_at for window in account.quota if window.observed_at is not None]
            block.append(detail(ago(max(observed) if observed else None)))
            if account.refresh_s:
                every = max(1, account.refresh_s // _SECONDS_PER_MINUTE)
                block.append(detail(translate("quota.info.every").format(minutes=every)))
            self.info_box.append(block)
        if not accounts:
            self.info_box.append(detail(translate("quota.source.none")))
        note = detail(translate("quota.info.note") + " " + translate("quota.info.mcp"))
        note.set_max_width_chars(42)
        self.info_box.append(note)

    def set_display(self, display: str) -> None:
        """Empty the content: the next `update_accounts` rebuilds it with the new choice."""
        if display != self._display:
            self._display = display
            self.set_title(translate("quota.title_left" if display == "remaining" else "quota.title"))
            while (child := self.content.get_first_child()) is not None:
                self.content.remove(child)

    def update_accounts(self, accounts: tuple[AccountView, ...], tabs: tuple[TabView, ...]) -> None:
        signature = tuple(
            (
                str(tab.id),
                str(tab.account_id) if tab.account_id is not None else None,
                tab.title,
                tab.session_cost_usd,
                tab.mcp_bytes,
            )
            for tab in tabs
        )
        if (
            (accounts, signature) == (self._accounts, self._tab_signature)
            and self.content.get_first_child() is not None
        ):
            return
        if self.refreshing and accounts != self._accounts:
            self._refresh_done()
        self._accounts, self._tab_signature, self._tabs = accounts, signature, tabs
        self._fill_info(accounts)
        while (child := self.content.get_first_child()) is not None:
            self.content.remove(child)
        self.window_rows.clear()
        self.account_cards.clear()
        for account in accounts:
            self.content.append(self._account_group(account))
        if not accounts:
            self.content.append(Gtk.Label(label=translate("quota.unknown.copy"), xalign=0, wrap=True))
        self._sync_panel()

    def _account_group(self, account: AccountView) -> Gtk.Button:
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
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
            if window.used_pct is None and window.spent_usd is None:
                group.append(detail(translate("quota.unknown.copy")))
        if not account.quota:
            group.append(detail(no_numbers_copy(account)))
        card = Gtk.Button(child=group)
        card.add_css_class("flat")
        card.add_css_class("ac-quota-group")
        if account.id == self.open_account_id:
            card.add_css_class("ac-quota-group-open")
        _ = card.connect("clicked", self._card_clicked, account.id)
        self.account_cards[account.id] = card
        return card

    def _card_clicked(self, _card: Gtk.Button, account_id: AccountId) -> None:
        self.open_account(account_id)

    def open_account(self, account_id: AccountId) -> None:
        """Draw that account's details on the right. Its card clicked again closes them."""
        if account_id == self.open_account_id:
            self.close_account()
            return
        self.open_account_id = account_id
        self._rebuild()

    def close_account(self) -> None:
        self.open_account_id = None
        self._rebuild()

    def _rebuild(self) -> None:
        for account_id, card in self.account_cards.items():
            if account_id == self.open_account_id:
                card.add_css_class("ac-quota-group-open")
            else:
                card.remove_css_class("ac-quota-group-open")
        self._sync_panel()

    def _sync_panel(self) -> None:
        account = next((item for item in self._accounts if item.id == self.open_account_id), None)
        # Hidden, not only folded: a folded revealer still kept part of the dialog's width.
        self.panel_revealer.set_visible(account is not None)
        self.panel_revealer.set_reveal_child(account is not None)
        self.set_content_width(_WIDTH if account is None else _WIDTH_WITH_PANEL)
        root = self.get_root()
        narrow = isinstance(root, Gtk.Widget) and 0 < root.get_width() < _ROOM_FOR_BOTH
        self.scroll.set_visible(account is None or not narrow)
        if account is not None:
            self.panel.show_account(account, self._tabs)

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
