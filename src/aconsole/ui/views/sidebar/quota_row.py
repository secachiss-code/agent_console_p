"""One contract-backed quota window, shared by the card and details."""

import locale
from datetime import UTC, datetime

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from aconsole.contracts.app import QuotaWindowView  # noqa: E402
from aconsole.ui.viewmodels.logic.quota_fmt import format_percent, quota_level  # noqa: E402
from aconsole.ui.viewmodels.logic.time_fmt import format_remaining  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402


class QuotaRow(Gtk.Box):
    """`compact`: name and percent over a thin bar, for a column of the card. Otherwise one line with the countdown."""

    def __init__(self, window: QuotaWindowView, *, compact: bool = False) -> None:
        super().__init__(spacing=3 if compact else 8)
        self.window: QuotaWindowView = window
        self.add_css_class("ac-quota-row")
        self.add_css_class(f"ac-quota-{quota_level(window.used_pct)}")
        self.key_label: Gtk.Label = Gtk.Label(label=translate(f"quota.{window.key}"), xalign=0)
        self.key_label.add_css_class("ac-quota-key")
        self.level_bar: Gtk.LevelBar = Gtk.LevelBar(min_value=0, max_value=1, hexpand=True)
        self.level_bar.set_value(window.used_pct / 100 if window.used_pct is not None else 0)
        self.level_bar.add_offset_value("low", 0.8)
        self.level_bar.add_offset_value("high", 0.95)
        self.level_bar.set_visible(window.used_pct is not None)
        if window.used_pct is None:
            self.set_tooltip_text(translate("quota.unknown.copy"))
        self.level_bar.set_valign(Gtk.Align.CENTER)
        self.percent_label: Gtk.Label = Gtk.Label(
            label=format_percent(window.used_pct, estimated=window.estimated), xalign=1,
        )
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
            self.key_label.set_width_chars(3)
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
