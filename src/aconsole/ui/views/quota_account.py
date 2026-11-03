"""One account in full, beside the limits: its plan, limits with exact resets, and tokens per model."""

import locale
from collections.abc import Callable
from datetime import UTC, datetime

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from aconsole.contracts.app import AccountView, ModelUsageView, QuotaWindowView, TabView  # noqa: E402
from aconsole.ui.viewmodels.logic.quota_fmt import (  # noqa: E402
    agent_name,
    format_money,
    format_percent,
    format_tokens,
)
from aconsole.ui.viewmodels.logic.time_fmt import format_remaining  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402

_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600
_SECONDS_PER_DAY = 86400
_SOURCES = ("claude-usage", "codex-app-server", "cursor-dashboard", "opencode-go")


def source_text(source: str | None) -> str:
    """Where an account's numbers come from, in words. An unknown code is shown as it is."""
    if source is None:
        return translate("quota.source.none")
    return translate(f"quota.source.{source}") if source in _SOURCES else source


def ago(observed: datetime | None) -> str:
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


def money_lines(account: AccountView, tabs: tuple[TabView, ...]) -> list[str]:
    """Money beside the limits: credits bought past the plan, and the open sessions' cost at API prices."""
    lines: list[str] = []
    if account.credits_unlimited:
        lines.append(translate("quota.credits_unlimited"))
    elif account.credits_balance is not None:
        lines.append(translate("quota.credits").format(count=f"{account.credits_balance:,.2f}"))
    costs = [tab.session_cost_usd for tab in tabs if tab.agent is account.agent and tab.session_cost_usd]
    if costs:
        # What this agent's open sessions would have cost at API prices: the money the plan stands in for.
        lines.append(translate("quota.api_cost").format(money=f"${sum(costs):,.2f}", count=len(costs)))
    return lines


def detail(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, wrap=True)
    label.add_css_class("ac-summary-detail")
    return label


def _heading(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class("caption-heading")
    return label


def _two_sides(left: str, right: str) -> Gtk.Box:
    line = Gtk.Box(spacing=8)
    name = Gtk.Label(label=left, xalign=0, hexpand=True, wrap=True)
    name.add_css_class("ac-summary-name")
    line.append(name)
    value = Gtk.Label(label=right, xalign=1)
    value.add_css_class("numeric")
    line.append(value)
    return line


def _reset_text(reset: datetime) -> str:
    language = locale.getlocale()[0] or "en"
    left = format_remaining(int((reset - datetime.now(UTC)).total_seconds()), language)
    return translate("quota.panel.reset").format(time=reset.astimezone().strftime("%d.%m.%Y %H:%M"), left=left)


def _limit_block(window: QuotaWindowView) -> Gtk.Box:
    block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    used = format_percent(window.used_pct, estimated=window.estimated)
    money = format_money(window.spent_usd, window.limit_usd)
    block.append(_two_sides(translate(f"quota.{window.key}"), f"{money} · {used}" if money else used))
    if window.period_days is not None:
        block.append(detail(translate("quota.panel.period").format(count=window.period_days)))
    if window.resets_at is not None:
        block.append(detail(_reset_text(window.resets_at)))
    return block


def _model_total(model: ModelUsageView) -> int:
    return model.input_tokens + model.output_tokens + model.cache_read_tokens + model.cache_write_tokens


def _model_block(model: ModelUsageView) -> Gtk.Box:
    block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    total = format_tokens(_model_total(model))
    cost = f" · ${model.cost_usd:,.2f}" if model.cost_usd is not None else ""
    block.append(_two_sides(model.model, total + cost))
    parts = (
        ("quota.panel.input", model.input_tokens),
        ("quota.panel.output", model.output_tokens),
        ("quota.panel.cache_read", model.cache_read_tokens),
        ("quota.panel.cache_write", model.cache_write_tokens),
    )
    block.append(detail(" · ".join(f"{translate(key)} {format_tokens(count)}" for key, count in parts if count)))
    return block


class AccountPanel(Gtk.Box):
    """The right side of the limits dialog. `show` draws one account; the close button is the caller's."""

    def __init__(self, on_close: Callable[[], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class("ac-quota-panel")
        header = Gtk.Box(spacing=8)
        self.title_label: Gtk.Label = Gtk.Label(xalign=0, hexpand=True)
        self.title_label.add_css_class("heading")
        header.append(self.title_label)
        self.close_button: Gtk.Button = Gtk.Button(icon_name="window-close-symbolic")
        self.close_button.add_css_class("flat")
        self.close_button.set_tooltip_text(translate("quota.panel.close"))
        self._on_close: Callable[[], None] = on_close
        _ = self.close_button.connect("clicked", self._close_clicked)
        header.append(self.close_button)
        self.append(header)
        self.body: Gtk.Box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.append(self.body)

    def _close_clicked(self, _button: Gtk.Button) -> None:
        self._on_close()

    def show_account(self, account: AccountView, tabs: tuple[TabView, ...]) -> None:
        self.title_label.set_label(agent_name(account.agent.value))
        while (child := self.body.get_first_child()) is not None:
            self.body.remove(child)
        self._plan(account)
        self.body.append(_heading(translate("quota.panel.limits")))
        for window in account.quota:
            self.body.append(_limit_block(window))
        if not account.quota:
            self.body.append(detail(translate("quota.unknown.copy")))
        for line in money_lines(account, tabs):
            self.body.append(detail(line))
        self._tokens(account)
        titles = ", ".join(tab.title for tab in tabs if tab.account_id == account.id)
        if titles:
            self.body.append(_heading(translate("quota.tabs")))
            self.body.append(detail(titles))
        self._source(account)

    def _plan(self, account: AccountView) -> None:
        if not account.plan_label and not account.plan_price:
            return
        self.body.append(_heading(translate("quota.panel.plan")))
        self.body.append(_two_sides(account.plan_label, account.plan_price or ""))

    def _tokens(self, account: AccountView) -> None:
        since = account.usage_since
        title = "quota.panel.tokens"
        self.body.append(
            _heading(
                translate(title) if since is None
                else translate("quota.panel.tokens_since").format(date=since.astimezone().strftime("%d.%m %H:%M")),
            ),
        )
        if not account.usage:
            self.body.append(detail(translate("quota.panel.no_tokens")))
            return
        total = format_tokens(sum(_model_total(model) for model in account.usage))
        costs = [model.cost_usd for model in account.usage if model.cost_usd is not None]
        cost = f" · ${sum(costs):,.2f}" if costs else ""
        self.body.append(_two_sides(translate("quota.panel.total"), total + cost))
        for model in account.usage:
            self.body.append(_model_block(model))

    def _source(self, account: AccountView) -> None:
        self.body.append(_heading(translate("quota.panel.source")))
        self.body.append(detail(source_text(account.source)))
        observed = [window.observed_at for window in account.quota if window.observed_at is not None]
        self.body.append(detail(ago(max(observed) if observed else None)))
