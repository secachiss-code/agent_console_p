"""What a person sees before the main window exists: progress, or why it did not come."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango  # noqa: E402

from aconsole.ui.client.error_text import core_log_path  # noqa: E402
from aconsole.ui.views.setup.messages import _ as translate  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable


class StartupWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title="AConsole", default_width=560, default_height=420)
        self.status: Adw.StatusPage = Adw.StatusPage(title=translate("startup.starting"))
        self.status.add_css_class("ac-empty")
        self.spinner: Gtk.Spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32)
        self.status.set_child(self.spinner)
        content = Adw.ToolbarView(content=self.status)
        content.add_top_bar(Adw.HeaderBar(show_title=False))
        self.set_content(content)
        self.reason_label: Gtk.Label | None = None
        self.log_label: Gtk.Label | None = None
        self.retry_button: Gtk.Button | None = None
        self.close_button: Gtk.Button | None = None

    def show_failure(self, reason: str, on_retry: Callable[[], None], on_close: Callable[[], None]) -> None:
        self.status.set_title(translate("startup.failed"))
        self.status.set_icon_name("dialog-error-symbolic")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER)
        self.reason_label = _selectable(reason)
        body.append(self.reason_label)
        self.log_label = _selectable(translate("startup.log").format(path=core_log_path()))
        self.log_label.add_css_class("dim-label")
        body.append(self.log_label)
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        self.close_button = Gtk.Button(label=translate("startup.close"))
        _ = self.close_button.connect("clicked", _pressed, on_close)
        buttons.append(self.close_button)
        self.retry_button = Gtk.Button(label=translate("startup.retry"))
        self.retry_button.add_css_class("suggested-action")
        _ = self.retry_button.connect("clicked", _pressed, on_retry)
        buttons.append(self.retry_button)
        body.append(buttons)
        self.status.set_child(body)
        self.present()

    def show_starting(self) -> None:
        self.status.set_title(translate("startup.starting"))
        self.status.set_icon_name(None)
        self.status.set_child(self.spinner)


def _pressed(_button: Gtk.Button, action: Callable[[], None]) -> None:
    action()


def _selectable(text: str) -> Gtk.Label:
    label = Gtk.Label(
        label=text, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True, justify=Gtk.Justification.CENTER,
    )
    label.set_focusable(False)
    return label
