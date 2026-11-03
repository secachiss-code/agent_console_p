"""One contract-backed quota window, shared by the card and details."""

import locale
from datetime import UTC, datetime

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from aconsole.contracts.app import AccountView, QuotaWindowView  # noqa: E402
from aconsole.ui.viewmodels.logic.quota_fmt import (  # noqa: E402
    agent_name,
    format_money,
    format_percent,
    peak_window,
    pool_size,
    quota_level,
    shown_percent,
)
from aconsole.ui.viewmodels.logic.time_fmt import format_remaining  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402


def window_name(window: QuotaWindowView) -> str:
    """What the window is called, with the pool's size when that is all the dollars known: "API $20"."""
    return " ".join(filter(None, (translate(f"quota.{window.key}"), pool_size(window.spent_usd, window.limit_usd))))


class QuotaRow(Gtk.Box):
    """`compact`: name and percent over a thin bar, for a column of the card. Otherwise one line with the countdown."""

    def __init__(self, window: QuotaWindowView, *, compact: bool = False, display: str = "used") -> None:
        super().__init__(spacing=3 if compact else 8)
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
        money = format_money(window.spent_usd, window.limit_usd)
        # A pool with no cap has no share to show: its dollars stand in for the percent.
        text = money if window.used_pct is None and money else format_percent(shown, estimated=window.estimated)
        self.percent_label: Gtk.Label = Gtk.Label(label=text, xalign=1)
        self.percent_label.add_css_class("numeric")
        self.percent_label.add_css_class("ac-quota-percent")
        self.time_label: Gtk.Label = Gtk.Label(xalign=1)
        self.time_label.add_css_class("numeric")
        self.time_label.add_css_class("ac-quota-key")
        self.time_label.set_visible(not compact)
        if compact:
            self.set_orientation(Gtk.Orientation.VERTICAL)
            line = Gtk.Box(spacing=4)
            self.key_label.set_hexpand(True)
            line.append(self.key_label)
            line.append(self.percent_label)
            self.append(line)
            self.append(self.level_bar)
        else:
            self.key_label.set_width_chars(5)
            self.percent_label.set_width_chars(5)
            self.time_label.set_width_chars(5)
            for child in (self.key_label, self.level_bar, self.percent_label, self.time_label):
                self.append(child)
        self.refresh_time()

    def refresh_time(self) -> None:
        reset = self.window.resets_at
        language = locale.getlocale()[0] or "en"
        remaining = int((reset - datetime.now(UTC)).total_seconds()) if reset else 0
        self.time_label.set_label(format_remaining(remaining, language) if remaining > 0 else "—")
        tooltip = translate("quota.reset").format(time=reset.astimezone().strftime("%H:%M")) if reset else None
        self.time_label.set_tooltip_text(tooltip)
        if self.window.used_pct is not None:
            self.set_tooltip_text(tooltip)


class AccountLine(Gtk.Box):
    """One account on one line, for a card with three agents or more: its name, a thin bar per window, the peak."""

    def __init__(self, account: AccountView, *, display: str = "used") -> None:
        super().__init__(spacing=8)
        self.account: AccountView = account
        self.add_css_class("ac-account-line")
        self.name_label: Gtk.Label = Gtk.Label(label=agent_name(account.agent.value), xalign=0)
        self.name_label.set_width_chars(8)
        self.name_label.add_css_class("ac-summary-name")
        self.append(self.name_label)
        bars = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        self.bars: list[Gtk.LevelBar] = []
        for window in account.quota:
            if window.used_pct is None:
                continue
            bar = Gtk.LevelBar(min_value=0, max_value=1, hexpand=True)
            shown = shown_percent(window.used_pct, display) or 0.0
            bar.set_value(shown / 100)
            bar.add_offset_value("low", 0.8)
            bar.add_offset_value("high", 0.95)
            bar.add_css_class(f"ac-quota-{quota_level(window.used_pct)}")
            self.bars.append(bar)
            bars.append(bar)
        peak = peak_window(account.quota, lambda window: window.used_pct)
        self.peak_label: Gtk.Label = Gtk.Label(xalign=1)
        self.peak_label.set_width_chars(9)
        self.peak_label.add_css_class("numeric")
        self.peak_label.add_css_class("ac-quota-percent")
        if peak is None:
            empty = Gtk.Label(
                label=translate("quota.unknown"), xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
            )
            empty.add_css_class("ac-summary-detail")
            bars.append(empty)
        else:
            shown = shown_percent(peak.used_pct, display)
            self.peak_label.set_label(
                f"{window_name(peak)}  {format_percent(shown, estimated=peak.estimated)}",
            )
            self.add_css_class(f"ac-quota-{quota_level(peak.used_pct)}")
        self.append(bars)
        self.append(self.peak_label)
        self.set_tooltip_text(_line_tooltip(account, display))


def _line_tooltip(account: AccountView, display: str) -> str:
    parts = [agent_name(account.agent.value) + (f" · {account.plan_label}" if account.plan_label else "")]
    for window in account.quota:
        text = format_percent(shown_percent(window.used_pct, display), estimated=window.estimated)
        money = format_money(window.spent_usd, window.limit_usd)
        parts.append(f"{window_name(window)}: {text}" + (f" ({money})" if money else ""))
    if not account.quota:
        parts.append(translate("quota.unknown"))
    return "\n".join(parts)
