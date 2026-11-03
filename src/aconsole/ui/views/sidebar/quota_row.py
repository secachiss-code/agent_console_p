"""One contract-backed quota window, shared by the card and details."""

import locale
from datetime import UTC, datetime

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from aconsole.contracts.app import AccountView, QuotaWindowView  # noqa: E402
from aconsole.ui.viewmodels.logic.quota_fmt import (  # noqa: E402
    agent_name,
    format_percent,
    format_window_money,
    peak_window,
    quota_level,
    shown_percent,
)
from aconsole.ui.viewmodels.logic.time_fmt import format_remaining  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402


def window_name(window: QuotaWindowView) -> str:
    """What the window is called. The plan's main pool is named by its days, as "5 h" and "7 d" are; API stays API."""
    if window.period_days is not None and window.key != "api":
        return translate("quota.days").format(count=window.period_days)
    return translate(f"quota.{window.key}")


def _remaining(window: QuotaWindowView) -> str:
    reset = window.resets_at
    seconds = int((reset - datetime.now(UTC)).total_seconds()) if reset else 0
    return format_remaining(seconds, locale.getlocale()[0] or "en") if seconds > 0 else "—"


def _reset_tooltip(window: QuotaWindowView) -> str | None:
    reset = window.resets_at
    return translate("quota.reset").format(time=reset.astimezone().strftime("%d.%m %H:%M")) if reset else None


class QuotaRow(Gtk.Box):
    """One window on one line: its name, a bar, its money or percent, and the countdown."""

    def __init__(self, window: QuotaWindowView, *, display: str = "used") -> None:
        super().__init__(spacing=8)
        self.window: QuotaWindowView = window
        self.add_css_class("ac-quota-row")
        self.add_css_class(f"ac-quota-{quota_level(window.used_pct)}")
        self.key_label: Gtk.Label = Gtk.Label(label=window_name(window), xalign=0)
        self.key_label.add_css_class("ac-quota-key")
        self.level_bar: Gtk.LevelBar = Gtk.LevelBar(min_value=0, max_value=1, hexpand=True)
        shown = shown_percent(window.used_pct, display)
        self.level_bar.set_value(shown / 100 if shown is not None else 0)
        self.level_bar.add_offset_value("low", 0.8)
        self.level_bar.add_offset_value("high", 0.95)
        self.level_bar.set_visible(window.used_pct is not None)
        if window.used_pct is None:
            self.set_tooltip_text(translate("quota.unknown.copy"))
        self.level_bar.set_valign(Gtk.Align.CENTER)
        # Dollars, when they are known, say more than a share of them. Not for Auto + Composer: its pool is not a sum
        # anyone buys, so its dollars stay in the account's details.
        money = "" if window.key == "auto" else format_window_money(window.spent_usd, window.limit_usd, display)
        self.percent_label: Gtk.Label = Gtk.Label(
            label=money or format_percent(shown, estimated=window.estimated), xalign=1,
        )
        self.percent_label.add_css_class("numeric")
        self.percent_label.add_css_class("ac-quota-percent")
        self.time_label: Gtk.Label = Gtk.Label(xalign=1)
        self.time_label.add_css_class("numeric")
        self.time_label.add_css_class("ac-quota-key")
        # Wide enough for "Неделя" and "$391.54 / $450", so every bar in the list has one width.
        self.key_label.set_width_chars(7)
        self.percent_label.set_width_chars(15)
        self.time_label.set_width_chars(5)
        for child in (self.key_label, self.level_bar, self.percent_label, self.time_label):
            self.append(child)
        self.refresh_time()

    def refresh_time(self) -> None:
        self.time_label.set_label(_remaining(self.window))
        tooltip = _reset_tooltip(self.window)
        self.time_label.set_tooltip_text(tooltip)
        if self.window.used_pct is not None:
            self.set_tooltip_text(tooltip)


class AccountLine(Gtk.Box):
    """One account on one line: its name, one bar for the window closest to its limit, the time until that resets."""

    def __init__(self, account: AccountView, *, display: str = "used") -> None:
        super().__init__(spacing=8)
        self.account: AccountView = account
        self.add_css_class("ac-account-line")
        self.name_label: Gtk.Label = Gtk.Label(label=agent_name(account.agent.value), xalign=0)
        self.name_label.set_width_chars(8)
        self.name_label.add_css_class("ac-summary-name")
        self.append(self.name_label)
        self.peak: QuotaWindowView | None = peak_window(account.quota, lambda window: window.used_pct)
        self.level_bar: Gtk.LevelBar = Gtk.LevelBar(
            min_value=0, max_value=1, hexpand=True, valign=Gtk.Align.CENTER,
        )
        self.level_bar.add_offset_value("low", 0.8)
        self.level_bar.add_offset_value("high", 0.95)
        self.percent_label: Gtk.Label = Gtk.Label(xalign=1)
        self.percent_label.set_width_chars(5)
        self.percent_label.add_css_class("numeric")
        self.percent_label.add_css_class("ac-quota-percent")
        self.time_label: Gtk.Label = Gtk.Label(xalign=1)
        self.time_label.set_width_chars(5)
        self.time_label.add_css_class("numeric")
        self.time_label.add_css_class("ac-quota-key")
        if self.peak is None:
            empty = Gtk.Label(
                label=translate("quota.unknown"), xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
            )
            empty.add_css_class("ac-summary-detail")
            self.append(empty)
        else:
            shown = shown_percent(self.peak.used_pct, display) or 0.0
            self.level_bar.set_value(shown / 100)
            self.level_bar.add_css_class(f"ac-quota-{quota_level(self.peak.used_pct)}")
            self.add_css_class(f"ac-quota-{quota_level(self.peak.used_pct)}")
            self.percent_label.set_label(format_percent(shown, estimated=self.peak.estimated))
            self.append(self.level_bar)
            self.append(self.percent_label)
            self.append(self.time_label)
        self.set_tooltip_text(_line_tooltip(account, display))
        self.refresh_time()

    def refresh_time(self) -> None:
        if self.peak is not None:
            self.time_label.set_label(_remaining(self.peak))


def _line_tooltip(account: AccountView, display: str) -> str:
    parts = [agent_name(account.agent.value) + (f" · {account.plan_label}" if account.plan_label else "")]
    for window in account.quota:
        text = format_percent(shown_percent(window.used_pct, display), estimated=window.estimated)
        money = format_window_money(window.spent_usd, window.limit_usd, display)
        parts.append(f"{window_name(window)}: {text}" + (f" ({money})" if money else ""))
    if not account.quota:
        parts.append(translate("quota.unknown"))
    return "\n".join(parts)
