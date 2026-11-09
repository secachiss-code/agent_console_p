"""Deterministic native widgets for visual regression tests; no running agent or clock."""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import override

import gi
import msgspec

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from aconsole.contracts.common import RiskTag  # noqa: E402
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot  # noqa: E402
from aconsole.ui import i18n  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.snapshot import capture_window  # noqa: E402
from aconsole.ui.style import SPACE, stylesheet  # noqa: E402
from aconsole.ui.viewmodels.approvals_vm import ApprovalsViewModel  # noqa: E402
from aconsole.ui.views.approvals.panel import ApprovalCard, ApprovalPanel  # noqa: E402
from aconsole.ui.views.sidebar.plan_card import PlanCard  # noqa: E402
from aconsole.ui.views.sidebar.plan_graph import PlanGraph  # noqa: E402
from aconsole.ui.views.sidebar.quota_card import QuotaCard  # noqa: E402


def gallery(width: int) -> Gtk.Widget:
    data = demo_snapshot()
    columns = Gtk.Box(spacing=SPACE["xl"])
    summary = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=280)
    summary.add_css_class("ac-summary-card")
    plan = PlanCard(lambda _plan: None)
    plan.set_plan(data.plans[0])
    summary.append(plan)
    summary.append(Gtk.Separator())
    quota = QuotaCard()
    quota.set_accounts(data.accounts[:2])
    summary.append(quota)
    if width > 700:
        columns.append(summary)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, spacing=SPACE["lg"])
    columns.append(body)
    graph = PlanGraph(data.plans[0], lambda: None)
    graph.set_vexpand(True)
    body.append(graph)
    panel = ApprovalPanel(ApprovalsViewModel(CoreClient("/unused.sock")), on_toast=lambda _text: None,
                          on_open=lambda _tab: None, on_left=lambda: None)
    source = msgspec.structs.replace(data.approvals[0], expires_at=datetime(2099, 1, 1, tzinfo=UTC))
    card = ApprovalCard(source, "DES-3", panel)
    card.timer.set_label("Осталось 8:51")
    # The exact same risk widget as a live card, including precedence of mixed risks.
    risks = Gtk.Box(spacing=SPACE["md"])
    for risk in (RiskTag.NETWORK, RiskTag.OUTSIDE_WORKTREE):
        sample = ApprovalCard(msgspec.structs.replace(source, risk=frozenset({risk})), "DES-3", panel)
        badge = sample._risks()
        assert badge is not None
        risks.append(badge)
    body.append(risks)
    body.append(card)
    return columns


class Preview(Adw.Application):
    def __init__(self, theme: str, width: int, output: Path) -> None:
        super().__init__(application_id="io.github.aconsole.StylePreview", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.theme = theme
        self.width = width
        self.output = output
        self.window: Adw.ApplicationWindow | None = None
        self.failure: Exception | None = None

    @override
    def do_activate(self) -> None:
        settings = Gtk.Settings.get_default()
        assert settings is not None
        settings.set_property("gtk-enable-animations", False)  # noqa: FBT003
        settings.set_property("gtk-font-name", "DejaVu Sans 10")
        settings.set_property("gtk-icon-theme-name", "Adwaita")
        settings.set_property("gtk-decoration-layout", "")
        Adw.StyleManager.get_default().set_color_scheme(
            Adw.ColorScheme.FORCE_DARK if self.theme == "dark" else Adw.ColorScheme.FORCE_LIGHT,
        )
        provider = Gtk.CssProvider()
        provider.load_from_string(stylesheet())
        display = Gdk.Display.get_default()
        assert display is not None
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window = Adw.ApplicationWindow(application=self, default_width=self.width, default_height=800)
        self.window.set_content(gallery(self.width))
        self.window.present()
        self.window.set_focus(None)
        _ = GLib.timeout_add(400, self._arm)

    def _arm(self) -> bool:
        assert self.window is not None
        clock = self.window.get_frame_clock()
        assert clock is not None
        handle = clock.connect("after-paint", self._capture)
        self._paint_handle = handle
        self.window.queue_draw()
        return GLib.SOURCE_REMOVE

    def _capture(self, clock: Gdk.FrameClock) -> None:
        clock.disconnect(self._paint_handle)
        assert self.window is not None
        try:
            capture_window(self.window, self.output)
        except (RuntimeError, GLib.Error) as error:
            self.failure = error
        self.window.destroy()
        self.quit()


def main() -> None:
    # Locale and clock stabilization belong only to this fixture.
    i18n.locale.getlocale = lambda: ("ru_RU", "UTF-8")
    theme, width, output = sys.argv[1:]
    app = Preview(theme, int(width), Path(output))
    status = app.run(["aconsole-style-preview"])
    if app.failure is not None:
        raise app.failure
    raise SystemExit(status)


if __name__ == "__main__":
    main()
