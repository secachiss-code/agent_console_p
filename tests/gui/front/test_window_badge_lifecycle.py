"""A disposed window cannot keep or restart its periodic Git subscription."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio

from tests.gui.front.test_b5a_page import _PageTestApp, _run_ui, _until


def test_badge_timer_stops_on_disposal_and_cannot_restart_on_remap() -> None:
    from gi.repository import GLib

    from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
    from aconsole.ui.viewmodels.tabs_vm import TabsViewModel
    from aconsole.ui.views.window import MainWindow

    async def scenario(app: _PageTestApp) -> None:
        app.client.store.apply_snapshot(demo_snapshot(), 0)
        window = MainWindow(app, TabsViewModel(app.client))
        app.window = window
        window.present()
        await _until(lambda: bool(window.terminal_pages))
        for page in window.terminal_pages.values():
            if page.worker is not None:
                await asyncio.wait_for(page.worker, timeout=5.0)
        model = window.tabs.model
        model.items_changed(0, 0, 0)
        timer = window._git_badge_timer
        assert timer is not None
        context = GLib.MainContext.default()
        source = context.find_source_by_id(timer)
        assert source is not None
        watch_calls = sum(method == "git/watch" for method, _params in app.client.calls)
        source.set_ready_time(0)
        await _until(lambda: sum(method == "git/watch" for method, _params in app.client.calls) > watch_calls)
        assert context.find_source_by_id(timer) is not None
        model.items_changed(0, 0, 0)
        assert window._git_badge_timer == timer

        unrelated = GLib.timeout_add(60_000, lambda: GLib.SOURCE_CONTINUE)
        try:
            window.unrealize()
            assert window._git_badge_timer is None
            assert context.find_source_by_id(timer) is None
            calls = len(app.client.calls)
            window.realize()
            model.items_changed(0, 0, 0)
            assert window._keep_git_badges() == GLib.SOURCE_REMOVE
            await asyncio.sleep(0)
            assert window._git_badge_timer is None
            assert len(app.client.calls) == calls
            assert context.find_source_by_id(unrelated) is not None
            window.unrealize()
        finally:
            _ = GLib.source_remove(unrelated)

    _run_ui(scenario)
