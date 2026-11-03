"""B5a terminal page lifecycle and per-tab page retention."""

from __future__ import annotations

import asyncio
import tempfile
from typing import TYPE_CHECKING, override

import gi
import msgspec

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from aconsole.contracts.app import TerminalLaunchSpec  # noqa: E402
from aconsole.contracts.common import TabId  # noqa: E402
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabItem, TabsViewModel  # noqa: E402
from aconsole.ui.views.window import MainWindow  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path


_SCENARIO_TIMEOUT_SECONDS = 10.0


class _TerminalClient(CoreClient):
    def __init__(self, spec: TerminalLaunchSpec) -> None:
        super().__init__("unused")
        self.spec = spec
        self.calls: list[tuple[str, object]] = []

    @override
    async def request[T: msgspec.Struct](
        self,
        method: str,
        params: msgspec.Struct,
        result_type: type[T],
    ) -> T:
        self.calls.append((method, params))
        if method == "tab/launchSpec":
            return msgspec.convert(self.spec, type=result_type)
        return result_type()


class _PageTestApp(Adw.Application):
    def __init__(self, scenario: Callable[[_PageTestApp], Awaitable[None]]) -> None:
        super().__init__(
            application_id="io.github.aconsole.B5aPageTest",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.scenario = scenario
        self.client = _TerminalClient(
            TerminalLaunchSpec(
                argv=("/usr/bin/true",),
                env={},
                cwd=tempfile.gettempdir(),
                scrollback_lines=0,
                title="terminal page test",
            )
        )
        self.window: Gtk.Window | None = None
        self.error: Exception | None = None

    @override
    def do_activate(self) -> None:
        self.hold()
        _ = spawn_task(self._run_scenario())

    async def _run_scenario(self) -> None:
        try:
            await asyncio.wait_for(self.scenario(self), timeout=_SCENARIO_TIMEOUT_SECONDS)
        except Exception as error:  # noqa: BLE001 -- surface asynchronous GTK failures to pytest.
            self.error = error
        finally:
            if self.window is not None:
                self.window.close()
            self.release()
            self.quit()


def _run_ui(scenario: Callable[[_PageTestApp], Awaitable[None]]) -> None:
    app = _PageTestApp(scenario)
    assert run_app(lambda: app, ["aconsole-b5a-page-test"]) == 0
    assert app.error is None, repr(app.error)


async def _until(predicate: Callable[[], bool]) -> None:
    async def poll() -> None:
        while not predicate():  # noqa: ASYNC110 -- GTK mapping and child completion are event-driven.
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout=5.0)


def _buttons(box: Gtk.Box) -> list[Gtk.Button]:
    buttons: list[Gtk.Button] = []
    child = box.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button):
            buttons.append(child)
        child = child.get_next_sibling()
    return buttons


def test_b5a_terminal_page_starts_once_and_shows_three_actions_after_exit(
    tmp_path: Path,
) -> None:
    from aconsole.ui.views.terminal.page import TerminalPage

    async def scenario(app: _PageTestApp) -> None:
        app.client.spec = msgspec.structs.replace(app.client.spec, cwd=str(tmp_path))
        snapshot = demo_snapshot()
        app.client.store.apply_snapshot(snapshot, 0)
        tab_id = TabId(str(snapshot.tabs[0].id))
        created: list[int] = []
        closed: list[str] = []
        page = TerminalPage(
            app.client,
            tab_id,
            on_new=lambda: created.append(1),
            on_close=closed.append,
        )
        window = Adw.ApplicationWindow(application=app)
        app.window = window
        window.set_content(page)
        window.present()

        # The bar follows the terminal's own history: output past one screen gives it something to scroll.
        adjustment = page.scrollbar.get_adjustment()
        assert adjustment is page.terminal.get_vadjustment()
        page.terminal.feed(b"line\r\n" * 500)
        await _until(lambda: adjustment.get_upper() > adjustment.get_page_size())

        assert page.worker is None
        page.start()
        worker = page.worker
        assert worker is not None
        page.start()
        assert page.worker is worker
        await _until(page.ended_revealer.get_reveal_child)
        await asyncio.wait_for(worker, timeout=5.0)

        methods = [method for method, _params in app.client.calls]
        assert methods == ["tab/launchSpec", "tab/started", "tab/exited"]
        assert page.ended_revealer.get_reveal_child()
        assert len(_buttons(page.ended_actions)) == 3
        assert not page.error_label.get_visible()
        assert created == []
        assert closed == []
        page.close()

    _run_ui(scenario)


def test_b5a_closed_terminal_page_does_not_start_queued_worker() -> None:
    from aconsole.ui.views.terminal.page import TerminalPage

    async def scenario(app: _PageTestApp) -> None:
        snapshot = demo_snapshot()
        app.client.store.apply_snapshot(snapshot, 0)
        page = TerminalPage(
            app.client,
            TabId(str(snapshot.tabs[0].id)),
            on_new=lambda: None,
            on_close=lambda _tab_id: None,
        )

        page.start()
        worker = page.worker
        assert worker is not None
        page.close()
        await asyncio.wait_for(worker, timeout=1.0)

        assert not any(method == "tab/launchSpec" for method, _params in app.client.calls)

    _run_ui(scenario)


def test_b5a_window_creates_terminal_pages_on_selection_and_reuses_them() -> None:
    async def scenario(app: _PageTestApp) -> None:
        snapshot = demo_snapshot()
        app.client.store.apply_snapshot(snapshot, 0)
        window = MainWindow(app, TabsViewModel(app.client))
        app.window = window
        window.present()
        await _until(lambda: window.selection.get_selected_item() is not None)
        selected_item = window.selection.get_selected_item()
        assert isinstance(selected_item, TabItem)

        first_index = window.selection.get_selected()
        first_tab_id = str(selected_item.tab_id)
        await _until(lambda: first_tab_id in window.terminal_pages)
        first_page = window.terminal_pages[first_tab_id]
        assert all(
            tab_id == first_tab_id for tab_id in window.terminal_pages
        )

        second_index = (first_index + 1) % len(snapshot.tabs)
        model = window.selection.get_model()
        second_item = model.get_item(second_index)
        assert isinstance(second_item, TabItem)
        second_tab_id = str(second_item.tab_id)
        window.selection.set_selected(second_index)
        await _until(lambda: second_tab_id in window.terminal_pages)
        second_page = window.terminal_pages[second_tab_id]
        assert second_page is not first_page

        window.selection.set_selected(first_index)
        await _until(lambda: window.terminal_stack.get_visible_child() is first_page)
        assert window.terminal_pages[first_tab_id] is first_page
        assert window.terminal_pages[second_tab_id] is second_page

        for page in window.terminal_pages.values():
            if page.worker is not None:
                await asyncio.wait_for(page.worker, timeout=5.0)
            page.close()

    _run_ui(scenario)


def test_b5a_disposed_window_ignores_later_store_and_selection_updates() -> None:
    from aconsole.contracts.events import TabAdded
    from aconsole.contracts.ipc import EventEnvelope

    async def scenario(app: _PageTestApp) -> None:
        snapshot = demo_snapshot()
        app.client.store.apply_snapshot(snapshot, 0)
        window = MainWindow(app, TabsViewModel(app.client))
        app.window = window
        window.present()
        await _until(lambda: bool(window.terminal_pages))
        for page in window.terminal_pages.values():
            if page.worker is not None:
                await asyncio.wait_for(page.worker, timeout=5.0)
        original_pages = dict(window.terminal_pages)

        window.unrealize()
        new_tab = msgspec.structs.replace(
            snapshot.tabs[0],
            id=TabId("01J9Z5W6FX1M2A3B4C5D6E7F8H"),
            order=max(tab.order for tab in snapshot.tabs) + 1,
            title="Added after window disposal",
        )
        assert app.client.store.apply(
            EventEnvelope(seq=1, at=new_tab.status_since, event=TabAdded(tab=new_tab))
        )

        model = window.selection.get_model()
        new_position = -1
        for index in range(model.get_n_items()):
            item = model.get_item(index)
            if isinstance(item, TabItem) and str(item.tab_id) == str(new_tab.id):
                new_position = index
                break
        assert new_position >= 0
        window.selection.set_selected(new_position)

        assert window.terminal_pages == original_pages
        assert str(new_tab.id) not in window.terminal_pages

    _run_ui(scenario)
