"""The approval card against the fake core: decisions, focus, closed requests, and its place on screen."""

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

from aconsole.contracts.app import ApprovalView, CommandDetail, EditDetail, GenericDetail  # noqa: E402
from aconsole.contracts.common import ApprovalDecision, ApprovalId, ApprovalScope, RiskTag, TabId  # noqa: E402
from aconsole.core.infrastructure.fake.server import FakeCore  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.viewmodels.approvals_vm import ApprovalsViewModel  # noqa: E402
from aconsole.ui.views.app import AConsoleApp  # noqa: E402
from aconsole.ui.views.approvals.panel import ApprovalPanel  # noqa: E402
from tests.gui.watchdog import arm_watchdog  # noqa: E402

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
            _ = arm_watchdog(self, _TIMEOUT_MS, self._expired)
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
                first = pulled.snapshot.approvals[0].tab_id
                panel.update(pulled.snapshot.approvals, {str(first): "tab"}, first)
                seen.now += 1.0  # past the wait for a card that has just appeared
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


def _expanders(widget: Gtk.Widget) -> list[Gtk.Expander]:
    found: list[Gtk.Expander] = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Expander):
            found.append(child)
        found.extend(_expanders(child))
        child = child.get_next_sibling()
    return found


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
@pytest.mark.parametrize("kind", ["command", "edit", "generic"])
def test_full_approval_text_can_be_read_without_losing_the_decision_buttons(core: str, kind: str) -> None:
    async def check(panel: ApprovalPanel, _seen: _Seen, window: Gtk.Window) -> None:
        source = panel.cards[0].view
        full = "x" * 4500 + "\nEND OF REQUEST\x1b[2K"
        if kind == "command":
            detail = CommandDetail(command=full, args_preview=None)
        elif kind == "edit":
            detail = EditDetail(files=tuple(f"file-{number}.py" for number in range(10)),
                diff_preview="-before\n+after", input_json=full)
        else:
            detail = GenericDetail(json_preview=full)
        view = msgspec.structs.replace(source, id=ApprovalId("full-request"), detail=detail)
        window.set_default_size(700, 800)
        panel.update((view,), {}, view.tab_id)
        card = panel.cards[0]
        assert "END OF REQUEST" not in "\n".join(_labels(card))
        expanders = _expanders(card.details)
        assert expanders
        for expander in expanders:
            expander.set_expanded(True)
        await _settled(lambda: "END OF REQUEST" in "\n".join(_labels(card)))
        labels = "\n".join(_labels(card))
        assert "END OF REQUEST[U+001B][2K" in labels
        assert "\x1b" not in labels
        if kind == "edit":
            assert "file-9.py" in labels
            assert "-before\n+after" in labels
            assert any(text in labels for text in ("Changes protected files", "Меняет защищённые файлы"))
        await _settled(lambda: card.allow_button.get_height() > 0)
        assert card.details.get_height() <= 180
        assert card.allow_button.get_mapped()
        assert card.deny_button.get_mapped()
        assert card.release_button.get_mapped()

    _on_panel(core, check)


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
def test_cards_show_what_is_asked_and_take_no_focus(core: str) -> None:
    async def check(panel: ApprovalPanel, _seen: _Seen, _window: Gtk.Window) -> None:
        assert panel.get_visible()
        assert len(panel.cards) == 2
        first = _labels(panel.cards[0])
        assert "rm -rf build" in first
        assert any("/wt/1" in label for label in first)
        assert any("a.py" in label for label in _labels(panel.cards[1]))
        assert panel.cards[0].session_check is not None
        assert panel.cards[1].session_check is None
        assert panel.cards[0].has_css_class("ac-attention-card")
        assert panel.focused_index() is None
        assert panel.shown is panel.cards[0]
        assert panel.stack.get_visible_child() is panel.cards[0]

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
        assert panel.shown is panel.cards[0]
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


def _other_tab(view: ApprovalView, number: int) -> ApprovalView:
    return msgspec.structs.replace(view, id=f"{view.id[:-1]}{number}", tab_id=TabId(f"{view.tab_id}-other"))


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
def test_only_the_open_tab_shows_its_request_and_the_rest_are_counted(core: str) -> None:
    async def check(panel: ApprovalPanel, _seen: _Seen, _window: Gtk.Window) -> None:
        here = panel.cards[0].view
        others = (_other_tab(here, 7), _other_tab(here, 8))
        panel.update((here, *others), {}, here.tab_id)
        assert [card.view.id for card in panel.cards] == [here.id]
        assert "2" in panel.more_label.get_label()
        assert panel.more_label.get_visible()
        panel.update((here,), {}, here.tab_id)
        assert not panel.more_label.get_visible()
        panel.update(others, {}, here.tab_id)
        assert not panel.cards
        assert not panel.get_visible()

    _on_panel(core, check)


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
def test_a_risky_allow_waits_half_a_second_and_is_never_the_default(core: str) -> None:
    async def check(panel: ApprovalPanel, _seen: _Seen, _window: Gtk.Window) -> None:
        risky, plain = panel.cards
        assert RiskTag.DESTRUCTIVE in risky.view.risk
        assert not risky.allow_button.has_css_class("suggested-action")
        assert plain.allow_button.has_css_class("suggested-action")
        assert not risky.allow_button.get_sensitive()
        await asyncio.sleep(0.3)
        assert not risky.allow_button.get_sensitive()
        await _settled(risky.allow_button.get_sensitive)

    _on_panel(core, check)


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
def test_a_new_card_cannot_be_decided_by_the_press_that_met_it(core: str) -> None:
    async def check(panel: ApprovalPanel, seen: _Seen, _window: Gtk.Window) -> None:
        first, second = panel.cards
        panel.update((second.view,), {}, second.view.tab_id)
        assert panel.shown is second
        panel.decide(second, ApprovalDecision.DENY)
        await asyncio.sleep(0.05)
        assert not second.busy
        assert first not in panel.cards
        seen.now += 1.0
        panel.decide(second, ApprovalDecision.DENY)
        assert second.busy

    _on_panel(core, check)


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
def test_the_checkbox_widens_the_allow_to_the_tab(core: str) -> None:
    async def check(panel: ApprovalPanel, _seen: _Seen, _window: Gtk.Window) -> None:
        card = panel.cards[0]
        sent: list[ApprovalScope] = []

        def spy(target: object, decision: ApprovalDecision, scope: ApprovalScope = ApprovalScope.ONCE) -> None:
            sent.append(scope)
            _ = (target, decision)

        panel.decide = spy  # type: ignore[method-assign]
        await _settled(card.allow_button.get_sensitive)
        card.allow_button.emit("clicked")
        assert card.session_check is not None
        card.session_check.set_active(True)
        card.allow_button.emit("clicked")
        assert sent == [ApprovalScope.ONCE, ApprovalScope.TAB_SESSION]

    _on_panel(core, check)


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
def test_tab_reaches_the_details_the_checkbox_and_every_button(core: str) -> None:
    async def check(panel: ApprovalPanel, _seen: _Seen, window: Gtk.Window) -> None:
        card = panel.cards[0]
        await _settled(card.allow_button.get_sensitive)
        assert panel.step(1)
        reached: list[Gtk.Widget | None] = [window.get_focus()]
        for _ in range(6):
            _ = window.child_focus(Gtk.DirectionType.TAB_FORWARD)
            reached.append(window.get_focus())
        for stop in (card.details, card.session_check, card.release_button, card.deny_button, card.allow_button):
            assert stop in reached

    _on_panel(core, check)


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
def test_buttons_keep_one_row_against_the_end_when_there_is_room(core: str) -> None:
    async def check(panel: ApprovalPanel, _seen: _Seen, window: Gtk.Window) -> None:
        window.set_default_size(900, 500)
        await asyncio.sleep(0.2)
        card = panel.cards[0]
        buttons = (card.release_button, card.deny_button, card.allow_button)
        spots = [button.compute_bounds(window)[1] for button in buttons]
        assert len({spot.get_y() for spot in spots}) == 1
        assert spots[0].get_x() < spots[1].get_x() < spots[2].get_x()
        assert spots[2].get_x() + spots[2].get_width() > window.get_width() - 40

    _on_panel(core, check)


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
def test_expired_approval_disables_allow_and_blocks_keyboard_decision(core: str) -> None:
    async def check(panel: ApprovalPanel, seen: _Seen, _window: Gtk.Window) -> None:
        card = panel.cards[0]
        now = datetime.now(UTC)
        card.view = msgspec.structs.replace(card.view, expires_at=now + timedelta(seconds=1))
        card.tick(now)
        await _settled(card.allow_button.get_sensitive)
        card.view = msgspec.structs.replace(card.view, expires_at=now)
        card.tick(now)
        assert not card.allow_button.get_sensitive()
        assert card.session_check is not None
        assert not card.session_check.get_sensitive()
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
        _ = arm_watchdog(self, _TIMEOUT_MS, self._expired)
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
        await _past_guard(panel)
        assert _press(window, Gdk.KEY_d)
        await _settled(lambda: first not in panel.cards)
        assert panel.focused_index() == 0
        await _past_guard(panel)
        assert _press(window, Gdk.KEY_Return)
        selected = window.selection.get_selected_item()
        assert selected is not None
        assert selected.tab_id == panel.cards[0].view.tab_id
        assert panel.focused_index() is None


class _ScreenApp(_WindowApp):
    """Card over the real terminal, in the narrowest window there is, asked about the longest command."""

    async def _check(self, window: MainWindow) -> None:
        panel = window.approval_panel
        await _settled(lambda: len(panel.cards) == 2)
        window.set_default_size(360, 640)
        await asyncio.sleep(0.3)
        covered = window.terminal_stack
        with_card = (covered.get_width(), covered.get_height())
        waiting = panel._approvals
        selected = panel._tab
        panel.update((), {}, selected)
        await asyncio.sleep(0.2)
        assert (covered.get_width(), covered.get_height()) == with_card
        huge = "npm publish " * 4000
        view = msgspec.structs.replace(
            waiting[0], summary=huge, detail=CommandDetail(command=huge, args_preview=huge),
        )
        panel.update((view,), {}, selected)
        await asyncio.sleep(0.3)
        assert (covered.get_width(), covered.get_height()) == with_card
        assert window.get_focus() is None or panel.focused_index() is None
        card = panel.cards[0]
        for button in (card.release_button, card.deny_button, card.allow_button):
            found, bounds = button.compute_bounds(window)
            assert found
            assert bounds.get_y() >= 0
            assert bounds.get_x() >= 0
            assert bounds.get_y() + bounds.get_height() <= window.get_height()
            assert bounds.get_x() + bounds.get_width() <= window.get_width()
        scroll = card.details.get_vadjustment()
        assert scroll.get_page_size() < scroll.get_upper()


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


async def _past_guard(panel: ApprovalPanel) -> None:
    await _settled(lambda: panel._clock() >= panel._guard_until)


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
def test_the_card_leaves_the_terminal_alone_and_its_buttons_on_screen(core: str) -> None:
    app = _ScreenApp(CoreClient(core))
    assert run_app(lambda: app, ["approval-screen-test"]) == 0
    if app.failure is not None:
        raise app.failure


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
def test_the_window_routes_keys_to_a_card_only_when_it_has_focus(core: str) -> None:
    app = _WindowApp(CoreClient(core))
    assert run_app(lambda: app, ["approval-window-test"]) == 0
    if app.failure is not None:
        raise app.failure
