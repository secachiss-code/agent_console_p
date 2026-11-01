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
    def __init__(self, window: QuotaWindowView) -> None:
        super().__init__(spacing=6)
        self.window: QuotaWindowView = window
        self.add_css_class(f"ac-quota-{quota_level(window.used_pct)}")
        self.key_label: Gtk.Label = Gtk.Label(label=translate(f"quota.{window.key}"), xalign=0)
        self.key_label.set_width_chars(3)
        self.append(self.key_label)
        self.level_bar: Gtk.LevelBar = Gtk.LevelBar(min_value=0, max_value=1, hexpand=True)
        self.level_bar.set_value(window.used_pct / 100 if window.used_pct is not None else 0)
        self.level_bar.add_offset_value("low", 0.8)
        self.level_bar.add_offset_value("high", 0.95)
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
