"""System notifications for requests: one per episode, gone on a decision, a click opens the tab."""

from __future__ import annotations

import asyncio
import tempfile
from typing import TYPE_CHECKING

import gi
import msgspec
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gio, GLib  # noqa: E402

from aconsole.contracts.common import TabId  # noqa: E402
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot  # noqa: E402
from aconsole.core.infrastructure.fake.server import FakeCore  # noqa: E402
from aconsole.ui.bootstrap import run_app  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.viewmodels.approval_alerts import Alert, ApprovalAlerts  # noqa: E402
from aconsole.ui.views.app import AConsoleApp  # noqa: E402
from aconsole.ui.views.approvals.alert import OPEN_APPROVAL_ACTION, alert_body, alert_notification  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Iterator

    from aconsole.contracts.app import ApprovalView
    from aconsole.ui.views.window import MainWindow

_TIMEOUT_MS = 8000
_FIRST = TabId("tab-first")
_SECOND = TabId("tab-second")


def _request(tab: TabId, number: int = 0) -> ApprovalView:
    view = demo_snapshot().approvals[0]
    return msgspec.structs.replace(view, tab_id=tab, id=type(view.id)(f"{view.id}{number}"))


class _Bell:
    def __init__(self) -> None:
        self.posted: list[Alert] = []
        self.withdrawn: list[str] = []
        self.alerts: ApprovalAlerts = ApprovalAlerts(self.posted.append, self.withdrawn.append)

    def see(self, *views: ApprovalView, active: bool = False, selected: TabId | None = _FIRST) -> None:
        self.alerts.update(views, {"tab-first": "One"}, window_active=active, active_tab_id=selected)


def test_a_request_in_an_inactive_window_rings_once_with_the_tab() -> None:
    bell = _Bell()
    bell.see(_request(_FIRST))
    bell.see(_request(_FIRST), _request(_FIRST, 1))
    assert [(alert.tab_id, alert.tab_title) for alert in bell.posted] == [(_FIRST, "One")]
    assert bell.posted[0].id.startswith(f"approval-{_FIRST}-")


def test_an_active_window_does_not_ring() -> None:
    bell = _Bell()
    bell.see(_request(_FIRST), active=True)
    assert bell.posted == []


def test_an_inactive_tab_rings_in_an_active_window() -> None:
    bell = _Bell()
    bell.see(_request(_FIRST), _request(_SECOND), active=True)
    assert [alert.tab_id for alert in bell.posted] == [_SECOND]
    bell.see(_request(_FIRST), _request(_SECOND), active=True)
    assert len(bell.posted) == 1


def test_switching_to_a_notified_tab_withdraws_without_repeating_its_episode() -> None:
    bell = _Bell()
    bell.see(_request(_SECOND), active=True)
    bell.see(_request(_SECOND), active=True, selected=_SECOND)
    assert bell.withdrawn == [bell.posted[0].id]
    bell.see(_request(_SECOND), active=True)
    assert len(bell.posted) == 1


def test_an_active_window_without_a_selected_tab_does_not_hide_requests() -> None:
    bell = _Bell()
    bell.see(_request(_FIRST), active=True, selected=None)
    assert [alert.tab_id for alert in bell.posted] == [_FIRST]


def test_a_decision_withdraws_and_the_next_request_is_a_new_episode() -> None:
    bell = _Bell()
    bell.see(_request(_FIRST))
    bell.see()
    assert bell.withdrawn == [bell.posted[0].id]
    bell.see(_request(_FIRST, 1))
    assert len(bell.posted) == 2
    assert bell.posted[1].id != bell.posted[0].id


def test_coming_back_withdraws_and_leaving_again_does_not_ring_again() -> None:
    bell = _Bell()
    bell.see(_request(_FIRST))
    bell.see(_request(_FIRST), active=True)
    assert bell.withdrawn == [bell.posted[0].id]
    bell.see(_request(_FIRST))
    assert len(bell.posted) == 1
    bell.see()
    assert len(bell.withdrawn) == 1


def test_leaving_a_window_with_an_open_request_rings_once() -> None:
    bell = _Bell()
    bell.see(_request(_FIRST), active=True)
    bell.see(_request(_FIRST))
    assert len(bell.posted) == 1


def test_each_tab_has_its_own_episode_and_only_the_decided_one_goes() -> None:
    bell = _Bell()
    bell.see(_request(_FIRST), _request(_SECOND))
    assert len(bell.posted) == 2
    bell.see(_request(_SECOND))
    assert bell.withdrawn == [bell.posted[0].id]


def test_a_tab_title_that_is_unknown_still_rings() -> None:
    bell = _Bell()
    bell.see(_request(_SECOND))
    assert bell.posted[0].tab_title == ""


def test_a_closing_window_takes_its_notifications_along() -> None:
    bell = _Bell()
    bell.see(_request(_FIRST))
    bell.alerts.clear()
    assert bell.withdrawn == [bell.posted[0].id]


def test_the_notification_does_not_show_the_command() -> None:
    view = msgspec.structs.replace(_request(_FIRST), summary="curl -H 'Token: SECRET' x", tool="Bash\x1b[2J")
    notification = alert_notification(Alert(id="a", tab_id=_FIRST, tab_title="One", view=view))
    assert isinstance(notification, Gio.Notification)
    assert "SECRET" not in _body(view)
    assert "\x1b" not in _body(view)


def _body(view: ApprovalView) -> str:
    return alert_body(Alert(id="a", tab_id=_FIRST, tab_title="One", view=view))


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


class _WindowApp(AConsoleApp):
    def __init__(self, client: CoreClient) -> None:
        super().__init__(client)
        self.failure: BaseException | None = None
        self.sent: list[str] = []
        self.gone: list[str] = []

    def send_notification(self, notification_id: str | None, _notification: Gio.Notification) -> None:
        self.sent.append(str(notification_id))

    def withdraw_notification(self, notification_id: str) -> None:
        self.gone.append(notification_id)

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
        store = window.tabs.client.store
        snapshot = store.snapshot
        assert snapshot is not None
        assert snapshot.approvals
        await asyncio.sleep(0.2)
        assert self.sent == [], "a window that is opening must not ring"
        window.is_active = lambda: False  # type: ignore[method-assign]
        window.get_mapped = lambda: True  # type: ignore[method-assign]
        window.notify("is-active")
        assert len(self.sent) == len({view.tab_id for view in snapshot.approvals})
        ringing = self.sent[0]
        tab = next(iter(view.tab_id for view in snapshot.approvals))
        window.tabs.client.store.apply_snapshot(msgspec.structs.replace(snapshot, approvals=()), store.seq + 1)
        await asyncio.sleep(0.05)
        assert ringing in self.gone
        for index in range(window.selection.get_n_items()):
            item = window.selection.get_item(index)
            if getattr(item, "tab_id", None) != tab:
                window.selection.set_selected(index)
                break
        self.activate_action(OPEN_APPROVAL_ACTION, GLib.Variant("s", str(tab)))
        await asyncio.sleep(0.05)
        selected = window.selection.get_selected_item()
        assert selected is not None
        assert str(selected.tab_id) == str(tab)
        # A new request in a different tab must ring even while the window is active.
        other_index = next(
            index for index in range(window.selection.get_n_items())
            if getattr(window.selection.get_item(index), "tab_id", None) != tab
        )
        window.selection.set_selected(other_index)
        window.is_active = lambda: True  # type: ignore[method-assign]
        before = len(self.sent)
        view = next(view for view in snapshot.approvals if view.tab_id == tab)
        store.apply_snapshot(msgspec.structs.replace(snapshot, approvals=(view,)), store.seq + 1)
        assert len(self.sent) == before + 1
        other_tab_alert = self.sent[-1]
        window._sync_approvals(store)
        assert len(self.sent) == before + 1
        self.activate_action(OPEN_APPROVAL_ACTION, GLib.Variant("s", str(tab)))
        await asyncio.sleep(0.05)
        assert other_tab_alert in self.gone
        assert len(self.sent) == before + 1


@pytest.mark.parametrize("core", ["waiting"], indirect=True)
def test_the_window_rings_withdraws_and_a_click_opens_the_tab(core: str) -> None:
    app = _WindowApp(CoreClient(core))
    assert run_app(lambda: app, ["approval-alert-test"]) == 0
    if app.failure is not None:
        raise app.failure
