"""The approval panel against the fake core: cards, decisions, focus, and closed requests."""

from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import gi
import msgspec
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from aconsole.contracts.common import ApprovalDecision, TabId  # noqa: E402
from aconsole.core.infrastructure.fake.server import FakeCore  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.viewmodels.approvals_vm import ApprovalsViewModel  # noqa: E402
from aconsole.ui.views.app import AConsoleApp  # noqa: E402
from aconsole.ui.views.approvals.panel import ApprovalPanel  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from aconsole.ui.views.window import MainWindow

_TIMEOUT_MS = 8000


class _Seen:
    def __init__(self) -> None:
        self.toasts: list[str] = []
        self.opened: list[TabId] = []
        self.left: int = 0
        self.now: float = 100.0

    def leave(self) -> None:
        self.left += 1


@pytest.fixture
def core(request: pytest.FixtureRequest) -> Iterator[str]:
    directory = tempfile.mkdtemp(prefix="aconsole-gui-")
    path = f"{directory}/core.sock"
    fake = FakeCore(path, request.param)
    fake.start()
    try:
        yield path
    finally:
        fake.stop()


def _on_panel(socket: str, check: Callable[[ApprovalPanel, _Seen, Gtk.Window], Awaitable[None]]) -> None:
    failures: list[BaseException] = []

    class _App(Adw.Application):
        def __init__(self) -> None:
            super().__init__(application_id="io.github.aconsole.ApprovalTest")
            self._held: bool = False

        def do_activate(self) -> None:
            if self._held:
                return
            self._held = True
            self.hold()
            _ = GLib.timeout_add(_TIMEOUT_MS, self._expired)
            _ = spawn_task(self._run())

        def _expired(self) -> bool:
            failures.append(TimeoutError("the panel did not settle"))
            self.quit()
            return GLib.SOURCE_REMOVE

        async def _run(self) -> None:
            client = CoreClient(socket)
            seen = _Seen()
            panel: ApprovalPanel | None = None
            try:
                _ = await client.connect()
                pulled, _subscribed = await client.pull_snapshot()
                panel = ApprovalPanel(
                    ApprovalsViewModel(client),
                    on_toast=seen.toasts.append,
                    on_open=seen.opened.append,
                    on_left=seen.leave,
                    clock=lambda: seen.now,
                )
                window = Adw.ApplicationWindow(application=self, content=panel)
                window.present()
                panel.update(pulled.snapshot.approvals, {str(pulled.snapshot.approvals[0].tab_id): "tab"})
                await check(panel, seen, window)
            except BaseException as error:  # noqa: BLE001 -- reported after the loop stops
                failures.append(error)
            finally:
                if panel is not None:
                    panel.stop()
                await client.close()
                self.release()
                self.quit()

    assert run_app(_App, ["approval-test"]) == 0
    if failures:
        raise failures[0]


async def _settled(check: Callable[[], bool]) -> None:
    for _ in range(200):
        if check():
            return
        await asyncio.sleep(0.02)
    message = "condition not reached"
    raise AssertionError(message)


def _labels(widget: Gtk.Widget) -> list[str]:
    found: list[str] = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            found.append(child.get_label())
        found.extend(_labels(child))
        child = child.get_next_sibling()
    return found


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
def test_cards_show_what_is_asked_and_take_no_focus(core: str) -> None:
    async def check(panel: ApprovalPanel, _seen: _Seen, window: Gtk.Window) -> None:
        assert panel.get_visible()
        assert len(panel.cards) == 2
        first = _labels(panel.cards[0])
        assert "rm -rf build" in first
        assert any("/wt/1" in label for label in first)
        assert any("a.py" in label for label in _labels(panel.cards[1]))
        assert panel.cards[0].session_button is not None
        assert panel.cards[1].session_button is None
        assert panel.cards[0].has_css_class("ac-attention-card")
        assert panel.focused_index() is None
        assert window.get_focus() is None or not isinstance(window.get_focus(), Gtk.ListBoxRow)
        panel.toggle_button.set_active(False)
        assert not panel.requests_scroll.get_visible()
        assert panel.step(1)
        assert panel.requests_scroll.get_visible()

    _on_panel(core, check)


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
def test_a_decision_closes_the_card_and_the_next_press_cannot_land_on_the_next_one(core: str) -> None:
    async def check(panel: ApprovalPanel, seen: _Seen, _window: Gtk.Window) -> None:
        assert panel.step(1)
        assert panel.focused_index() == 0
        panel.decide_focused(ApprovalDecision.ALLOW)
        panel.decide_focused(ApprovalDecision.ALLOW)
        await _settled(lambda: len(panel.cards) == 1)
        assert panel.focused_index() == 0
        panel.decide_focused(ApprovalDecision.ALLOW)
        await asyncio.sleep(0.1)
        assert len(panel.cards) == 1
        assert not panel.cards[0].busy
        seen.now += 1.0
        panel.decide_focused(ApprovalDecision.DENY)
        await _settled(lambda: not panel.cards)
        assert not panel.get_visible()
        assert seen.left == 1
        assert seen.toasts == []

    _on_panel(core, check)


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
def test_keys_move_between_cards_and_open_the_tab(core: str) -> None:
    async def check(panel: ApprovalPanel, seen: _Seen, _window: Gtk.Window) -> None:
        panel.decide_focused(ApprovalDecision.ALLOW)
        await asyncio.sleep(0.05)
        assert len(panel.cards) == 2
        assert panel.step(1)
        assert panel.step(1)
        assert panel.focused_index() == 1
        assert panel.step(1)
        assert panel.focused_index() == 0
        assert panel.step(-1)
        assert panel.focused_index() == 1
        panel.open_focused()
        assert seen.opened == [panel.cards[1].view.tab_id]

    _on_panel(core, check)


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
def test_release_leaves_the_request_to_the_agent(core: str) -> None:
    async def check(panel: ApprovalPanel, seen: _Seen, _window: Gtk.Window) -> None:
        panel.release(panel.cards[0])
        await _settled(lambda: len(panel.cards) == 1)
        assert seen.left == 0
        assert seen.toasts == []

    _on_panel(core, check)


@pytest.mark.parametrize("core", ["approval-expired"], indirect=True)
def test_a_closed_request_says_the_decision_did_not_apply(core: str) -> None:
    async def check(panel: ApprovalPanel, seen: _Seen, _window: Gtk.Window) -> None:
        panel.decide(panel.cards[0], ApprovalDecision.ALLOW)
        await _settled(lambda: len(panel.cards) == 1)
        assert len(seen.toasts) == 1

    _on_panel(core, check)


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
def test_a_lost_call_keeps_the_card_for_another_try(core: str) -> None:
    async def check(panel: ApprovalPanel, seen: _Seen, _window: Gtk.Window) -> None:
        await panel.viewmodel.client.close()
        card = panel.cards[0]
        panel.decide(card, ApprovalDecision.ALLOW)
        await _settled(lambda: bool(seen.toasts))
        assert card in panel.cards
        assert not card.busy

    _on_panel(core, check)


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
def test_expired_approval_disables_allow_and_blocks_keyboard_decision(core: str) -> None:
    async def check(panel: ApprovalPanel, seen: _Seen, _window: Gtk.Window) -> None:
        card = panel.cards[0]
        now = datetime.now(UTC)
        card.view = msgspec.structs.replace(card.view, expires_at=now + timedelta(seconds=1))
        card.tick(now)
        assert card.allow_button.get_sensitive()
        card.view = msgspec.structs.replace(card.view, expires_at=now)
        card.tick(now)
        assert not card.allow_button.get_sensitive()
        assert card.session_button is not None
        assert not card.session_button.get_sensitive()
        assert panel.step(1)
        panel.decide_focused(ApprovalDecision.ALLOW)
        await asyncio.sleep(0)
        assert not card.busy
        assert len(panel.cards) == 2
        assert seen.toasts

    _on_panel(core, check)


class _WindowApp(AConsoleApp):
    """The whole window, with the panel reached through its registered shortcuts."""

    def __init__(self, client: CoreClient) -> None:
        super().__init__(client)
        self.failure: BaseException | None = None

    async def _open(self) -> None:
        _ = GLib.timeout_add(_TIMEOUT_MS, self._expired)
        try:
            await super()._open()
            assert self.window is not None
            await self._check(self.window)
        except BaseException as error:  # noqa: BLE001 -- reported after the loop stops
            self.failure = error
        finally:
            if self.window is not None:
                self.window.destroy()
            await self.client.close()
            self.quit()

    def _expired(self) -> bool:
        self.failure = self.failure or TimeoutError("the window did not settle")
        self.quit()
        return GLib.SOURCE_REMOVE

    async def _check(self, window: MainWindow) -> None:
        panel = window.approval_panel
        await _settled(lambda: len(panel.cards) == 2)
        # In the terminal, the single keys belong to the terminal.
        assert panel.focused_index() is None
        assert not _press(window, Gdk.KEY_a)
        assert not _press(window, Gdk.KEY_d)
        await asyncio.sleep(0.05)
        assert len(panel.cards) == 2
        assert _press(window, Gdk.KEY_y, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)
        assert panel.focused_index() == 0
        assert _press(window, Gdk.KEY_j)
        assert panel.focused_index() == 1
        assert _press(window, Gdk.KEY_k)
        assert panel.focused_index() == 0
        first = panel.cards[0]
        assert _press(window, Gdk.KEY_d)
        await _settled(lambda: first not in panel.cards)
        assert panel.focused_index() == 0
        for _ in range(100):
            await asyncio.sleep(0.02)
            if panel._clock() >= panel._guard_until:
                break
        assert _press(window, Gdk.KEY_Return)
        selected = window.selection.get_selected_item()
        assert selected is not None
        assert selected.tab_id == panel.cards[0].view.tab_id
        assert panel.focused_index() is None


def _press(window: MainWindow, keyval: int, modifiers: Gdk.ModifierType | None = None) -> bool:
    """What the shortcut controller does on a key. False means the key goes on to the focused widget."""
    wanted = Gdk.ModifierType(0) if modifiers is None else modifiers
    controller = window.shortcut_manager.controller
    for index in range(controller.get_n_items()):
        shortcut = controller.get_item(index)
        if not isinstance(shortcut, Gtk.Shortcut):
            continue
        trigger = shortcut.get_trigger()
        if not isinstance(trigger, Gtk.KeyvalTrigger):
            continue
        if (trigger.get_keyval(), trigger.get_modifiers()) == (keyval, wanted):
            action = shortcut.get_action()
            assert action is not None
            return action.activate(Gtk.ShortcutActionFlags.EXCLUSIVE, window, None)
    message = f"no shortcut for key {keyval}"
    raise AssertionError(message)


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
def test_the_window_routes_keys_to_a_card_only_when_it_has_focus(core: str) -> None:
    app = _WindowApp(CoreClient(core))
    assert run_app(lambda: app, ["approval-window-test"]) == 0
    if app.failure is not None:
        raise app.failure
