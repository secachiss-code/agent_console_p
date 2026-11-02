"""B2 tab sections follow live agent and account updates."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, override

import gi
import msgspec

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk, Pango  # noqa: E402

from aconsole.contracts.events import QuotaUpdated, TabUpdated  # noqa: E402
from aconsole.contracts.ipc import EventEnvelope  # noqa: E402
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabsViewModel  # noqa: E402
from aconsole.ui.views.tabs.row import TabRow  # noqa: E402
from aconsole.ui.views.tabs.sidebar import TabSidebar  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


_SCENARIO_TIMEOUT_SECONDS = 5.0
_WAIT_SECONDS = 2.0


class _SectionsTestApp(Adw.Application):
    """Run GTK assertions on the application's supported event loop."""

    def __init__(self, scenario: Callable[[_SectionsTestApp], Awaitable[None]]) -> None:
        super().__init__(
            application_id="io.github.aconsole.B2SectionsTest",
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
        except Exception as error:  # noqa: BLE001 -- report asynchronous GTK failures to pytest.
            self.error = error
        finally:
            if self.window is not None:
                self.window.close()
            self.release()
            self.quit()


def _run_ui(scenario: Callable[[_SectionsTestApp], Awaitable[None]]) -> None:
    app = _SectionsTestApp(scenario)
    assert run_app(lambda: app, ["aconsole-b2-sections-test"]) == 0
    assert app.error is None, repr(app.error)


async def _until(predicate: Callable[[], bool]) -> None:
    async def poll() -> None:
        while not predicate():  # noqa: ASYNC110 -- GTK mapping and header binding are event-driven.
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout=_WAIT_SECONDS)


def _walk(widget: Gtk.Widget) -> list[Gtk.Widget]:
    found = [widget]
    child = widget.get_first_child()
    while child is not None:
        found.extend(_walk(child))
        child = child.get_next_sibling()
    return found


def test_b2_tab_section_and_header_follow_tab_and_account_updates() -> None:
    snapshot = demo_snapshot()

    async def scenario(app: _SectionsTestApp) -> None:
        client = CoreClient("unused")
        client.store.apply_snapshot(snapshot, 0)
        tabs = TabsViewModel(client)
        sidebar = TabSidebar(tabs)
        assert app.window is not None
        app.window.set_default_size(520, 760)
        app.window.set_content(sidebar)
        app.window.present()
        await _until(sidebar.get_mapped)

        # Moving a middle Claude tab into the Codex section keeps its order fixed,
        # so the GTK section model must regroup based on the changed section key.
        source = sorted(snapshot.tabs, key=lambda tab: tab.order)[1]
        original_order = source.order
        moved_item = next(
            tabs.store.get(index)
            for index in range(tabs.store.get_n_items())
            if tabs.store.get(index).tab_id == str(source.id)
        )
        initial_position = next(
            index
            for index in range(sidebar.selection.get_model().get_n_items())
            if sidebar.selection.get_model().get_item(index) is moved_item
        )
        sidebar.selection.set_selected(initial_position)
        updated = msgspec.structs.replace(
            source,
            agent=snapshot.tabs[-1].agent,
            account_id=snapshot.tabs[-1].account_id,
        )
        assert updated.order == original_order
        assert client.store.apply(
            EventEnvelope(
                seq=1,
                at=updated.status_since,
                event=TabUpdated(tab=updated, changed=frozenset({"agent", "account_id"})),
            )
        )

        model = sidebar.selection.get_model()
        assert isinstance(model, Gtk.SortListModel)
        expected = sorted(
            (tabs.store.get(index) for index in range(tabs.store.get_n_items())),
            key=lambda item: (
                str(item.section_key),
                next(
                    tab.order
                    for tab in client.store.snapshot.tabs
                    if str(tab.id) == item.tab_id
                ),
            ),
        )
        await asyncio.sleep(0.1)
        actual = [model.get_item(index) for index in range(model.get_n_items())]
        regrouped = actual == expected
        selection_preserved = sidebar.selection.get_selected_item() is moved_item

        account = next(account for account in snapshot.accounts if account.id == updated.account_id)
        renamed_account = msgspec.structs.replace(account, plan_label="Team")
        assert client.store.apply(
            EventEnvelope(
                seq=2,
                at=updated.status_since,
                event=QuotaUpdated(account=renamed_account),
            )
        )

        expected_title = updated.agent.value.upper()
        await asyncio.sleep(0.1)
        header_titles = [
            widget.get_text()
            for widget in _walk(sidebar.tab_list)
            if isinstance(widget, Gtk.Label)
            and widget.has_css_class("ac-tab-section")
        ]
        header_updated = moved_item.section_title == expected_title and expected_title in header_titles
        assert regrouped, f"section regroup failed: headers={header_titles!r}"
        assert selection_preserved, "selection changed while regrouping tabs"
        assert header_updated, f"section header did not update: {header_titles!r}"

    _run_ui(scenario)


def test_b2_tab_row_edit_and_close_controls_have_explicit_behavior() -> None:
    snapshot = demo_snapshot()

    async def scenario(app: _SectionsTestApp) -> None:
        client = CoreClient("unused")
        client.store.apply_snapshot(snapshot, 0)
        tabs = TabsViewModel(client)
        sidebar = TabSidebar(tabs)
        assert app.window is not None
        app.window.set_default_size(520, 760)
        app.window.set_content(sidebar)
        app.window.present()
        await _until(sidebar.get_mapped)

        row = next(
            widget
            for widget in _walk(sidebar.tab_list)
            if isinstance(widget, TabRow) and widget.tab_id is not None
        )
        assert row.tab_id is not None
        assert row.subtitle_label.get_ellipsize() == Pango.EllipsizeMode.END
        assert row.subtitle_label.get_single_line_mode()
        assert not row.title_label.get_editable()

        # The menu's rename action is the only path that enables inline editing.
        assert row.activate_action("tab-row.rename", None)
        assert row.title_label.get_editing()
        assert row.title_label.get_editable()
        row.title_label.stop_editing(False)  # noqa: FBT003 -- cancel this test's rename edit.
        assert not row.title_label.get_editable()

        assert row.close_button.get_action_name() == "win.request-close-tab"
        target = row.close_button.get_action_target_value()
        assert target is not None
        assert target.unpack() == row.tab_id

    _run_ui(scenario)
