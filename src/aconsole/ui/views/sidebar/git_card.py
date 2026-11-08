"""The Git part of the sidebar summary: the project's worktrees counted, and the way to the Git page."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk, Pango  # noqa: E402

from aconsole.ui.viewmodels.git_vm import GitSummary  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402


def _detail() -> Gtk.Label:
    label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
    label.add_css_class("ac-summary-detail")
    label.add_css_class("numeric")
    return label


class GitCard(Gtk.Button):
    """A click opens Git & checkpoints, as the button it replaces did."""

    def __init__(self) -> None:
        super().__init__()
        self.update_property(
            [Gtk.AccessibleProperty.LABEL], [GObject.Value(GObject.TYPE_STRING, translate("git.card.open"))],
        )
        self.add_css_class("flat")
        self.add_css_class("ac-summary-section")
        self.set_action_name("win.open-git")
        self.summary: GitSummary | None = None
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        top = Gtk.Box(spacing=8)
        icon = Gtk.Image(icon_name="media-playlist-shuffle-symbolic", pixel_size=14)
        icon.add_css_class("ac-git-card-icon")
        top.append(icon)
        self.open_label: Gtk.Label = Gtk.Label(
            label=translate("git.card.open"), xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
        )
        self.open_label.add_css_class("ac-summary-name")
        top.append(self.open_label)
        box.append(top)
        self.count_label: Gtk.Label = _detail()
        box.append(self.count_label)
        self.state_label: Gtk.Label = _detail()
        box.append(self.state_label)
        self.set_child(box)
        self.set_summary(None)

    def set_summary(self, summary: GitSummary | None) -> None:
        self.summary = summary
        if summary is None:
            self.count_label.set_label(translate("git.card.unknown"))
            self.state_label.set_visible(False)
            return
        self.count_label.set_label(
            translate("git.card.worktrees").format(count=summary.worktrees, changed=summary.changed),
        )
        self.state_label.remove_css_class("warning")
        if summary.conflicted:
            self.state_label.set_label(translate("git.card.conflicts").format(count=summary.conflicted))
            self.state_label.add_css_class("warning")
        elif summary.ready:
            self.state_label.set_label(translate("git.card.ready").format(count=summary.ready))
        else:
            self.state_label.set_label(translate("git.card.nothing"))
        self.state_label.set_visible(True)
        self.set_tooltip_text(f"{self.count_label.get_label()}\n{self.state_label.get_label()}")
