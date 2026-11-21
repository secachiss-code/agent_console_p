"""Version and the packaged release notes."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from aconsole import __version__  # noqa: E402
from aconsole.ui.changelog import release_notes  # noqa: E402
from aconsole.ui.views.setup.messages import _ as translate  # noqa: E402


class AboutDialog(Adw.Dialog):
    def __init__(self) -> None:
        super().__init__(title=translate("about.title"), content_width=520, content_height=360)
        body = Gtk.Label(label=f"{__version__}\n\n{release_notes()}", wrap=True, xalign=0, yalign=0)
        body.set_margin_start(16)
        body.set_margin_end(16)
        body.set_margin_top(12)
        body.set_margin_bottom(16)
        scroll = Gtk.ScrolledWindow(child=body, propagate_natural_height=True)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(scroll)
        self.set_child(toolbar)
