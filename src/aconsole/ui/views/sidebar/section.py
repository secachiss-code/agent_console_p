"""One foldable section of the sidebar summary: a header that folds it, and the card under it."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk, Pango  # noqa: E402

from aconsole.ui.client.ui_state import UiState  # noqa: E402
from aconsole.ui.views.sidebar.messages import _ as translate  # noqa: E402

_FOLD_MS = 180


class SummarySection(Gtk.Box):
    """The header always shows the section's name and one short line, so a folded section still says something."""

    def __init__(self, name: str, title: str, body: Gtk.Widget, state: UiState) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("ac-summary-block")
        self.name: str = name
        self.body: Gtk.Widget = body
        self._state: UiState = state
        self.header: Gtk.Button = Gtk.Button()
        self.header.add_css_class("flat")
        self.header.add_css_class("ac-section-header")
        row = Gtk.Box(spacing=6)
        self.chevron: Gtk.Image = Gtk.Image(icon_name="pan-down-symbolic", pixel_size=12)
        self.chevron.add_css_class("ac-section-chevron")
        row.append(self.chevron)
        self.title_label: Gtk.Label = Gtk.Label(label=title.upper(), xalign=0)
        self.title_label.add_css_class("caption-heading")
        row.append(self.title_label)
        self.detail_label: Gtk.Label = Gtk.Label(xalign=1, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.detail_label.add_css_class("ac-section-detail")
        self.detail_label.add_css_class("numeric")
        row.append(self.detail_label)
        self.header.set_child(row)
        _ = self.header.connect("clicked", self._toggle)
        self.append(self.header)
        self.revealer: Gtk.Revealer = Gtk.Revealer(
            child=body, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=_FOLD_MS,
        )
        self.append(self.revealer)
        self._show(folded=state.folded(name))

    @property
    def folded(self) -> bool:
        return not self.revealer.get_reveal_child()

    def set_title(self, title: str) -> None:
        self.title_label.set_label(title.upper())

    def set_detail(self, text: str, level: str | None = None) -> None:
        self.detail_label.set_label(text)
        for name in ("warning", "error"):
            self.detail_label.remove_css_class(name)
        if level is not None:
            self.detail_label.add_css_class(level)

    def set_folded(self, *, folded: bool) -> None:
        self._show(folded=folded)
        self._state.set_folded(self.name, folded=folded)

    def _toggle(self, _button: Gtk.Button) -> None:
        self.set_folded(folded=not self.folded)

    def _show(self, *, folded: bool) -> None:
        self.revealer.set_reveal_child(not folded)
        if folded:
            self.add_css_class("ac-folded")
        else:
            self.remove_css_class("ac-folded")
        action = translate("section.expand" if folded else "section.collapse")
        self.header.set_tooltip_text(action)
        self.header.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [GObject.Value(GObject.TYPE_STRING, f"{self.title_label.get_label().capitalize()}: {action}")],
        )
        # GTK keeps "expanded" as a tristate, an int: a boolean value trips an assertion.
        self.header.update_state([Gtk.AccessibleState.EXPANDED], [GObject.Value(GObject.TYPE_INT, int(not folded))])
