"""Closing and saving preferences must preserve unconfirmed edits."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import msgspec
import pytest

from aconsole.contracts.app import SettingsUpdateParams
from aconsole.contracts.settings import Settings, SettingsPatch, commit_settings
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
from aconsole.ui.viewmodels.setup_vm import PreferencesViewModel
from aconsole.ui.views.app import AConsoleApp
from aconsole.ui.views.setup.preferences import PreferencesDialog
from aconsole.ui.views.sidebar.quota_card import QuotaCard
from tests.gui.front.test_b2_tabs import _model, _run_ui
from tests.gui.front.test_b2_tabs import _until as _wait

if TYPE_CHECKING:
    from collections.abc import Callable

    from gi.repository import Adw, Gtk

    from tests.gui.front.test_b2_tabs import _TabsTestApp


async def _until(predicate: Callable[[], bool]) -> None:
    try:
        await _wait(predicate)
    except TimeoutError as error:
        message = f"GTK wait failed at {getattr(getattr(predicate, '__code__', None), 'co_firstlineno', predicate)}"
        raise AssertionError(message) from error


async def _answer(prompt: Adw.AlertDialog, response: str) -> None:
    from gi.repository import Gtk

    await _until(prompt.get_mapped)
    label = prompt.get_response_label(response)
    def find(widget: Gtk.Widget) -> Gtk.Button | None:
        if isinstance(widget, Gtk.Button) and widget.get_label() == label:
            return widget
        child = widget.get_first_child()
        while child is not None:
            found = find(child)
            if found is not None:
                return found
            child = child.get_next_sibling()
        return None
    button = find(prompt)
    assert button is not None
    button.emit("clicked")




def test_closing_dirty_preferences_can_cancel_or_discard() -> None:
    async def scenario(app: _TabsTestApp) -> None:
        client, _tabs = _model(demo_snapshot())
        settings = Settings(rev=1, vault_path="", worktrees_dir="")

        async def request(_method: str, _params: msgspec.Struct, _result: type[msgspec.Struct]) -> msgspec.Struct:
            return settings

        client.request = request
        dialog = PreferencesDialog(PreferencesViewModel(client))
        assert app.window is not None
        app.window.set_default_size(800, 800)
        app.window.present()
        dialog.open(app.window)
        await _until(lambda: not dialog.loading)
        await _until(dialog.get_mapped)
        dialog.font_entry.set_text("Draft font")
        quitting = AConsoleApp(client)
        quitting.window = app.window
        app.window.preferences_dialog = dialog
        quitting._close_now(app.window)
        assert app.window.get_visible(), "application exit must not silently lose the draft"
        assert dialog._active
        prompt = dialog._close_prompt
        assert prompt is not None
        await _answer(prompt, "cancel")
        await _until(lambda: dialog._close_prompt is None)
        assert dialog.font_entry.get_text() == "Draft font"
        dialog.close()
        prompt = dialog._close_prompt
        assert prompt is not None
        await _answer(prompt, "discard")
        await _until(lambda: not dialog._active)
        await client.close()

    _run_ui(scenario)


async def _check_reopened_close(dialog: PreferencesDialog, parent: Gtk.Widget) -> None:
    dialog.open(parent)
    await _until(lambda: not dialog.loading and dialog.get_width() > 0)
    assert dialog._active
    dialog.close()
    await _until(lambda: not dialog.get_mapped())
    assert dialog._close_tick_id == 0
    assert dialog._unsubscribe is None


@pytest.mark.parametrize("decision", ["clean", "discard", "save", "clean-edited", "save-edited"])
def test_preferences_close_before_first_frame(decision: str) -> None:
    async def scenario(app: _TabsTestApp) -> None:
        client, _tabs = _model(demo_snapshot())
        settings = Settings(rev=1, vault_path="", worktrees_dir="")
        patches: list[SettingsPatch] = []
        action = decision.split("-", maxsplit=1)[0]

        async def request(method: str, params: msgspec.Struct, _result: type[msgspec.Struct]) -> msgspec.Struct:
            nonlocal settings
            if method == "settings/update":
                assert isinstance(params, SettingsUpdateParams)
                patches.append(params.patch)
                settings = commit_settings(settings, params.patch)
            return settings

        client.request = request
        dialog = PreferencesDialog(PreferencesViewModel(client))
        assert app.window is not None
        app.window.set_default_size(800, 800)
        app.window.present()
        dialog.open(app.window)
        # Deliver a confirmed close before GTK's opening frame callbacks run.
        assert dialog.get_width() == 0
        if action == "clean":
            dialog.close()
        else:
            assert dialog.worker is not None
            await dialog.worker
            dialog.font_entry.set_text("Early draft")
            dialog.close()
            prompt = dialog._close_prompt
            assert prompt is not None
            await _answer(prompt, action)
        if decision.endswith("-edited"):
            assert dialog.worker is not None
            await dialog.worker
            assert dialog._close_tick_id != 0
            dialog.font_entry.set_text("Later draft")
            await _until(lambda: dialog._close_tick_id == 0)
            assert dialog._active
            assert dialog.font_entry.get_text() == "Later draft"
            dialog.close()
            prompt = dialog._close_prompt
            assert prompt is not None
            await _answer(prompt, "discard")
        await _until(lambda: not dialog._active)
        await _until(lambda: not dialog.get_mapped())
        assert dialog._unsubscribe is None
        assert bool(patches) == (action == "save")
        assert settings.terminal_font == ("Early draft" if action == "save" else None)
        await _check_reopened_close(dialog, app.window)
        await client.close()

    _run_ui(scenario)


def test_hiding_parent_cancels_pending_preferences_close() -> None:
    async def scenario(app: _TabsTestApp) -> None:
        client, _tabs = _model(demo_snapshot())

        async def request(_method: str, _params: msgspec.Struct, _result: type[msgspec.Struct]) -> msgspec.Struct:
            return Settings(rev=1, vault_path="", worktrees_dir="")

        client.request = request
        dialog = PreferencesDialog(PreferencesViewModel(client))
        assert app.window is not None
        app.window.present()
        dialog.open(app.window)
        dialog.close()
        assert dialog._close_tick_id != 0
        app.window.set_visible(False)
        assert dialog._close_tick_id == 0
        app.window.present()
        await _check_reopened_close(dialog, app.window)
        await client.close()

    _run_ui(scenario)


def test_close_save_failure_retains_draft_and_late_save_cannot_claim_new_edits_saved() -> None:  # noqa: PLR0915 -- failure, late response, then confirmed close
    async def scenario(app: _TabsTestApp) -> None:
        client, _tabs = _model(demo_snapshot())
        settings = Settings(rev=1, vault_path="", worktrees_dir="")
        pending = asyncio.get_running_loop().create_future()
        patches = []

        async def request(method: str, params: msgspec.Struct, _result: type[msgspec.Struct]) -> msgspec.Struct:
            if method == "settings/get":
                return settings
            patches.append(params.patch)
            return await pending

        client.request = request
        dialog = PreferencesDialog(PreferencesViewModel(client))
        assert app.window is not None
        app.window.set_default_size(800, 800)
        app.window.present()
        dialog.open(app.window)
        await _until(lambda: not dialog.loading)
        await _until(dialog.get_mapped)
        dialog.font_entry.set_text("First draft")
        dialog.close()
        prompt = dialog._close_prompt
        assert prompt is not None
        await _answer(prompt, "save")
        await _until(lambda: bool(patches))
        pending.set_exception(RuntimeError("save failed"))
        assert dialog.worker is not None
        await dialog.worker
        assert dialog._active
        assert dialog.font_entry.get_text() == "First draft"
        pending = asyncio.get_running_loop().create_future()
        dialog.close()
        prompt = dialog._close_prompt
        assert prompt is not None
        await _answer(prompt, "save")
        await _until(lambda: len(patches) == 2)
        dialog.font_entry.set_text("Newer draft")
        pending.set_result(commit_settings(settings, patches[-1]))
        assert dialog.worker is not None
        await dialog.worker
        assert dialog._active
        assert dialog.font_entry.get_text() == "Newer draft"
        assert dialog._changes(dialog.viewmodel.settings) == SettingsPatch(terminal_font="Newer draft")
        pending = asyncio.get_running_loop().create_future()
        dialog.close()
        prompt = dialog._close_prompt
        assert prompt is not None
        await _answer(prompt, "save")
        await _until(lambda: len(patches) == 3)
        pending.set_result(commit_settings(dialog.viewmodel.settings, patches[-1]))
        assert dialog.worker is not None
        await dialog.worker
        await _until(lambda: not dialog._active)
        await client.close()

    _run_ui(scenario)


def test_custom_quota_thresholds_update_sidebar_and_open_details() -> None:
    async def scenario(app: _TabsTestApp) -> None:
        card = QuotaCard()
        account = demo_snapshot().accounts[0]
        windows = tuple(msgspec.structs.replace(window, used_pct=60.0) for window in account.quota)
        card.set_accounts((msgspec.structs.replace(account, quota=windows),))
        assert app.window is not None
        app.window.set_content(card)
        app.window.set_default_size(800, 800)
        app.window.present()
        details = card.open_details(app.window)
        card.set_display("remaining")
        card.set_thresholds(40, 50)
        assert card.account_lines[0].windows[0].has_css_class("ac-quota-critical")
        assert "40" in card.account_lines[0].windows[0].percent_label.get_text()
        assert details.window_rows[0].has_css_class("ac-quota-critical")
        card.set_thresholds(70, 90)
        assert card.account_lines[0].windows[0].has_css_class("ac-quota-ok")
        assert details.window_rows[0].has_css_class("ac-quota-ok")
        details.close()

    _run_ui(scenario)
