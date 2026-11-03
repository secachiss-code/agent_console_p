"""The core's counters, refreshed while the dialog is open."""

from typing import Final

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.viewmodels.health_vm import HealthFacts, HealthViewModel  # noqa: E402
from aconsole.ui.views.setup.messages import _ as translate  # noqa: E402

_REFRESH_S: Final = 2
_SLOW_MS: Final = 1000.0
_BACKLOG: Final = 100
_NO_VALUE: Final = "—"
_ROWS: Final = (
    "health.core",
    "health.queued",
    "health.failed",
    "health.commit",
    "health.answered",
    "health.slowest",
    "health.resyncs",
    "health.reconnects",
)


def _rows(facts: HealthFacts) -> dict[str, tuple[str, bool]]:
    """Each row's value, and whether it is a sign of trouble."""
    core = facts.core
    client = {
        "health.resyncs": (str(facts.resyncs), False),
        "health.reconnects": (str(facts.reconnects), False),
    }
    if core is None:
        empty = dict.fromkeys(_ROWS, (_NO_VALUE, False))
        return {**empty, "health.core": (translate("health.no_answer"), True), **client}
    state = "health.ok" if core.database else "health.no_database"
    return {
        "health.core": (translate(state), not core.database),
        "health.queued": (str(core.writer_queued), core.writer_queued >= _BACKLOG),
        "health.failed": (str(core.writer_failed), core.writer_failed > 0),
        "health.commit": (f"{core.writer_last_commit_ms:.1f}", core.writer_last_commit_ms >= _SLOW_MS),
        "health.answered": (str(core.ipc_answered), False),
        "health.slowest": (f"{core.ipc_slowest_ms:.1f}", core.ipc_slowest_ms >= _SLOW_MS),
        **client,
    }


class HealthDialog(Adw.Dialog):
    def __init__(self, viewmodel: HealthViewModel) -> None:
        super().__init__(title=translate("health.title"), content_width=420)
        self._viewmodel: HealthViewModel = viewmodel
        self._timer: int | None = None
        self.values: dict[str, Gtk.Label] = {}
        group = Adw.PreferencesGroup()
        for key in _ROWS:
            row = Adw.ActionRow(title=translate(key))
            value = Gtk.Label(label=_NO_VALUE, xalign=1)
            row.add_suffix(value)
            group.add(row)
            self.values[key] = value
        page = Adw.PreferencesPage()
        page.add(group)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(page)
        self.set_child(toolbar)
        _ = self.connect("map", self._mapped)
        _ = self.connect("unmap", self._unmapped)

    def show_facts(self, facts: HealthFacts) -> None:
        for key, (text, trouble) in _rows(facts).items():
            label = self.values[key]
            label.set_label(text)
            if trouble:
                label.add_css_class("error")
            else:
                label.remove_css_class("error")

    async def refresh(self) -> None:
        self.show_facts(await self._viewmodel.load())

    def _tick(self) -> bool:
        _ = spawn_task(self.refresh())
        return GLib.SOURCE_CONTINUE

    def _mapped(self, _widget: Gtk.Widget) -> None:
        _ = self._tick()
        if self._timer is None:
            self._timer = GLib.timeout_add_seconds(_REFRESH_S, self._tick)

    def _unmapped(self, _widget: Gtk.Widget) -> None:
        if self._timer is not None:
            _ = GLib.source_remove(self._timer)
            self._timer = None
