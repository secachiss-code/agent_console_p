"""B2 tab status tooltip and spinner presentation."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, override

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from aconsole.contracts.common import TabStatus  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabItem, TabsViewModel  # noqa: E402
from aconsole.ui.views.tabs.messages import _ as translate  # noqa: E402
from aconsole.ui.views.tabs.row import TabRow  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


_SCENARIO_TIMEOUT_SECONDS = 5.0


class _StatusTestApp(Adw.Application):
    """Run the GTK scenario on the application's supported event loop."""

    def __init__(self, scenario: Callable[[_StatusTestApp], Awaitable[None]]) -> None:
        super().__init__(
            application_id="io.github.aconsole.B2StatusTest",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.scenario = scenario
        self.window: Adw.ApplicationWindow | None = None
        self.error: Exception | None = None

    @override
    def do_activate(self) -> None:
        self.hold()
        _ = spawn_task(self._run_scenario())

    async def _run_scenario(self) -> None:
        try:
            self.window = Adw.ApplicationWindow(application=self)
            await asyncio.wait_for(self.scenario(self), timeout=_SCENARIO_TIMEOUT_SECONDS)
        except Exception as error:  # noqa: BLE001 -- surface asynchronous GTK failures to pytest.
            self.error = error
        finally:
            if self.window is not None:
                self.window.close()
            self.release()
            self.quit()


def _run_ui(scenario: Callable[[_StatusTestApp], Awaitable[None]]) -> None:
    app = _StatusTestApp(scenario)
    assert run_app(lambda: app, ["aconsole-b2-status-test"]) == 0
    assert app.error is None, repr(app.error)


def _item(status: TabStatus) -> TabItem:
    item = TabItem(f"test-{status.value}", status.value)
    item.status_code = status.value
    item.status_symbol = "◉" if status in {TabStatus.WAITING_APPROVAL, TabStatus.WAITING_INPUT} else "⟳"
    item.spinning = status is TabStatus.WORKING
    return item


def test_b2_status_tooltips_distinguish_waiting_states_and_show_working_spinner() -> None:
    async def scenario(app: _StatusTestApp) -> None:
        tabs = TabsViewModel(CoreClient("unused"))
        waiting_approval = _item(TabStatus.WAITING_APPROVAL)
        waiting_input = _item(TabStatus.WAITING_INPUT)
        working = _item(TabStatus.WORKING)
        waiting_approval.waiting_minutes = 4
        waiting_input.waiting_minutes = 0
        rows = [TabRow(tabs) for _ in range(3)]
        items = [waiting_approval, waiting_input, working]
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        assert app.window is not None
        app.window.set_content(container)
        app.window.present()
        try:
            for row, item in zip(rows, items, strict=True):
                row.bind_item(item)
                container.append(row)

            approval_row, input_row, working_row = rows
            assert waiting_approval.status_symbol == waiting_input.status_symbol == "◉"
            assert approval_row.status_label.get_tooltip_text() == translate(
                f"tabs.status.{TabStatus.WAITING_APPROVAL.value}"
            )
            assert input_row.status_label.get_tooltip_text() == translate(
                f"tabs.status.{TabStatus.WAITING_INPUT.value}"
            )
            assert (
                approval_row.status_label.get_tooltip_text()
                != input_row.status_label.get_tooltip_text()
            )
            assert working_row.status_spinner.get_visible()
            assert working_row.status_spinner.get_spinning()
            assert not working_row.status_label.get_visible()
            assert approval_row.waiting_label.get_visible()
            assert approval_row.waiting_label.get_text() == translate(
                "tabs.wait_minutes"
            ).format(minutes=4)
            assert input_row.waiting_label.get_visible()
            assert input_row.waiting_label.get_text() == translate(
                "tabs.wait_minutes"
            ).format(minutes=0)
            assert not working_row.waiting_label.get_visible()
        finally:
            for row in rows:
                row.unbind_item()

    _run_ui(scenario)
