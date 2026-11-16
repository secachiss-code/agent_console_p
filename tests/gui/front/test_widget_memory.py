"""Detached widgets must release callbacks, not accumulate across tab cycles."""

import asyncio
import gc
import weakref

from aconsole.contracts.common import TabId
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.viewmodels.tabs_vm import TabsViewModel
from aconsole.ui.views.tabs.row import TabRow
from aconsole.ui.views.terminal.page import TerminalPage
from tests.gui.front.test_b2_tabs import _run_ui, _TabsTestApp


def test_closed_terminal_pages_are_collected() -> None:
    async def scenario(app: _TabsTestApp) -> None:
        assert app.window is not None
        client = CoreClient("/unused-memory-test.sock")
        references: list[weakref.ReferenceType[TerminalPage]] = []
        for number in range(3):
            page = TerminalPage(client, TabId(f"tab-{number}"), lambda: None, lambda _tab: None)
            references.append(weakref.ref(page))
            app.window.set_content(page)
            app.window.present()
            await asyncio.sleep(.05)
            page.close()
            page.close()
            assert not page.clipboard.focused()
            app.window.set_content(None)
            del page
            await asyncio.sleep(.05)
            gc.collect()
        assert all(reference() is None for reference in references)

    _run_ui(scenario)


def test_disposed_tab_rows_are_collected() -> None:
    async def scenario(app: _TabsTestApp) -> None:
        assert app.window is not None
        model = TabsViewModel(CoreClient("/unused-memory-test.sock"))
        row = TabRow(model)
        reference = weakref.ref(row)
        app.window.set_content(row)
        app.window.present()
        await asyncio.sleep(.05)
        row.close()
        app.window.set_content(None)
        del row
        await asyncio.sleep(.05)
        gc.collect()
        assert reference() is None

    _run_ui(scenario)
