"""Close checks keep the window and running tabs intact until explicit confirmation."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

from aconsole.contracts.app import QuitCheckResult  # noqa: E402
from aconsole.contracts.common import TabId  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.views.app import AConsoleApp  # noqa: E402
from aconsole.ui.views.window import MainWindow  # noqa: E402


@pytest.mark.parametrize(("agents", "approvals"), [(0, 0), (2, 0), (0, 3), (2, 3)])
def test_quit_checks_counts_and_cancel_preserves_the_window(
    monkeypatch: pytest.MonkeyPatch, agents: int, approvals: int,
) -> None:
    app = AConsoleApp(CoreClient("unused"))
    window = Gtk.Window()
    closed: list[Gtk.Window] = []

    async def request(*_args: object) -> QuitCheckResult:
        return QuitCheckResult(busy_tabs=tuple(TabId(f"tab-{i}") for i in range(agents)), pending_approvals=approvals)

    monkeypatch.setattr(app.client, "request", request)
    monkeypatch.setattr(app, "_close_now", closed.append)
    try:
        asyncio.run(app._confirm_quit(window))
        dialog = app._quit_dialog
        if agents or approvals:
            assert dialog is not None
            assert dialog.get_default_response() == "cancel"
            assert str(agents) in dialog.get_body()
            assert str(approvals) in dialog.get_body()
            assert closed == []
            dialog.emit("response", "cancel")
            dialog.force_close()
            assert closed == []
            asyncio.run(app._confirm_quit(window))
            assert app._quit_dialog is not None
            confirmed = app._quit_dialog
            confirmed.emit("response", "close")
            confirmed.force_close()
        assert closed == [window]
    finally:
        window.destroy()


def test_unknown_core_state_requires_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    app = AConsoleApp(CoreClient("unused"))
    window = Gtk.Window()

    async def request(*_args: object) -> QuitCheckResult:
        raise ConnectionError

    monkeypatch.setattr(app.client, "request", request)
    try:
        asyncio.run(app._confirm_quit(window))
        assert app._quit_dialog is not None
        assert app._quit_dialog.get_default_response() == "cancel"
        dialog = app._quit_dialog
        dialog.emit("response", "cancel")
        dialog.force_close()
    finally:
        window.destroy()


@pytest.mark.parametrize(("running", "pending"), [(False, False), (True, False), (False, True)])
def test_clean_running_tab_is_not_closed_before_confirmation(*, running: bool, pending: bool) -> None:
    calls: list[str] = []

    async def request(*_args: object) -> QuitCheckResult:
        return QuitCheckResult(busy_tabs=(TabId("tab"),) if running else (), pending_approvals=0)

    async def close(tab_id: str, _decision: str | None) -> SimpleNamespace:
        calls.append(tab_id)
        return SimpleNamespace(needs_decision=False, closed=True, dirty_summary=None)

    # Use the real window routine with native GTK dialogs and a controlled core boundary.
    window = Gtk.Window()
    window._closing_tab_dialogs = {}
    window._checking_tab_close = set()
    window.tabs = SimpleNamespace(
        client=SimpleNamespace(request=request, store=SimpleNamespace(
            snapshot=SimpleNamespace(approvals=(SimpleNamespace(tab_id=TabId("tab")),)) if pending else None,
        )), close=close,
    )
    window._stop_tab_decided = lambda dialog, response, tab: MainWindow._stop_tab_decided(
        window, dialog, response, tab,
    )
    try:
        asyncio.run(MainWindow._close_tab_now(window, "tab", None))  # type: ignore[arg-type]
        assert calls == ([] if running or pending else ["tab"])
        if running or pending:
            dialog = window._closing_tab_dialogs["tab"]
            assert dialog.get_default_response() == "cancel"
            dialog.emit("response", "cancel")
            dialog.force_close()
            assert calls == []
            asyncio.run(MainWindow._close_tab_now(window, "tab", None, agent_confirmed=True))  # type: ignore[arg-type]
            assert calls == ["tab"]
    finally:
        window.destroy()


def test_repeated_window_close_uses_one_check(monkeypatch: pytest.MonkeyPatch) -> None:
    app = AConsoleApp(CoreClient("unused"))
    window = Gtk.Window()
    app.window = window  # type: ignore[assignment]
    closed: list[Gtk.Window] = []
    calls: list[str] = []

    async def drive() -> None:
        release = asyncio.Event()

        async def request(*_args: object) -> QuitCheckResult:
            calls.append("check")
            await release.wait()
            return QuitCheckResult(busy_tabs=(), pending_approvals=0)

        monkeypatch.setattr(app.client, "request", request)
        monkeypatch.setattr(app, "_close_now", closed.append)
        assert app._close_requested(window)
        assert app._close_requested(window)
        await asyncio.sleep(0)
        assert calls == ["check"]
        assert closed == []
        release.set()
        await asyncio.sleep(0)
        assert closed == [window]

    try:
        asyncio.run(drive())
    finally:
        window.destroy()
