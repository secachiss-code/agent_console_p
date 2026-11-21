"""Component versions and the packaged release notes."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango  # noqa: E402

from aconsole import CORE_VERSION, MCP_VERSION, REST_API_VERSION, UI_VERSION  # noqa: E402
from aconsole.ui.changelog import release_notes  # noqa: E402
from aconsole.ui.views.setup.messages import _ as translate  # noqa: E402

_RELEASES_URL = "https://github.com/secachiss-code/agent_console_p/releases"


class AboutDialog(Adw.Dialog):
    def __init__(self) -> None:
        super().__init__(title=translate("about.title"), content_width=520, content_height=460)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(12)
        content.set_margin_bottom(16)
        for message, version in (
            ("about.core", CORE_VERSION), ("about.ui", UI_VERSION),
            ("about.rest", REST_API_VERSION), ("about.mcp", MCP_VERSION),
        ):
            content.append(Gtk.Label(label=f"{translate(message)} {version}", xalign=0, wrap=True))
        content.append(Gtk.LinkButton(uri=_RELEASES_URL, label=translate("about.releases"), halign=Gtk.Align.START))
        content.append(
            Gtk.Label(label=release_notes(), wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, xalign=0, selectable=True)
        )
        scroll = Gtk.ScrolledWindow(child=content, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(scroll)
        self.set_child(toolbar)
