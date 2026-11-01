"""B4a plan card rendering and activation behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from aconsole.contracts.app import PlanView
    from aconsole.contracts.common import PlanId
    from aconsole.ui.views.window import MainWindow

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from aconsole.core.infrastructure.fake.scenarios import demo_snapshot  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.views.app import AConsoleApp  # noqa: E402


def _demo_plan() -> PlanView:
    snapshot = demo_snapshot()
    assert snapshot.active_project is not None
    return next(
        plan for plan in snapshot.plans
        if plan.project_id == snapshot.active_project and plan.status == "active"
    )


def _in_gtk_context(check: Callable[[], None]) -> None:
    """Run widget assertions after Adw.Application registration on GTK's loop."""
    failures: list[BaseException] = []

    class _WidgetTestApp(Adw.Application):
        def __init__(self) -> None:
            super().__init__(
                application_id="io.github.aconsole.AConsole.B4aTest",
                flags=Gio.ApplicationFlags.NON_UNIQUE,
            )
            self.timeout_id: int | None = None
            self.timed_out = False

        def do_activate(self) -> None:
            self.timeout_id = GLib.timeout_add_seconds(5, self._timeout)
            try:
                check()
            except BaseException as error:  # noqa: BLE001 - Preserve GTK assertion failures for the caller.
                failures.append(error)
            finally:
                if self.timeout_id is not None and not self.timed_out:
                    GLib.source_remove(self.timeout_id)
                    self.timeout_id = None
                self.quit()

        def _timeout(self) -> bool:
            self.timed_out = True
            failures.append(TimeoutError("B4a GUI assertion exceeded 5 seconds"))
            self.quit()
            return GLib.SOURCE_REMOVE

    app = _WidgetTestApp()
    status = run_app(lambda: app, ["aconsole-b4a-test"])
    assert status == 0
    if failures:
        raise failures[0]


def _run_window_probe(  # noqa: C901 - Keep lifecycle setup, timeout, and cleanup together for this GTK probe.
    running_app: str, check: Callable[[MainWindow], None]
) -> None:
    """Check a live fake-backed window and close its core client on every path."""
    failures: list[BaseException] = []

    class _WindowProbeApp(AConsoleApp):
        def __init__(self) -> None:
            super().__init__(CoreClient(running_app))
            self.watchdog_id: int | None = None
            self.poll_id: int | None = None

        def do_activate(self) -> None:
            super().do_activate()
            self.watchdog_id = GLib.timeout_add_seconds(10, self._watchdog)
            self.poll_id = GLib.timeout_add(10, self._check_window)

        def _check_window(self) -> bool:
            window = self.window
            if window is None:
                if self.failed:
                    failures.append(RuntimeError("AConsoleApp failed before creating MainWindow"))
                    self.poll_id = None
                return GLib.SOURCE_REMOVE if self.failed else GLib.SOURCE_CONTINUE
            self.poll_id = None
            try:
                check(window)
            except BaseException as error:  # noqa: BLE001 - Preserve GTK assertion failures for the caller.
                failures.append(error)
            finally:
                _ = window.emit("close-request")
            return GLib.SOURCE_REMOVE

        def _watchdog(self) -> bool:
            self.watchdog_id = None
            failures.append(TimeoutError("B4a MainWindow probe exceeded 10 seconds"))
            window = self.window
            _ = window.emit("close-request") if window is not None else spawn_task(self._finish())
            return GLib.SOURCE_REMOVE

    app = _WindowProbeApp()
    status = run_app(lambda: app, ["aconsole-b4a-window-test"])
    if app.poll_id is not None:
        GLib.source_remove(app.poll_id)
    if app.watchdog_id is not None:
        GLib.source_remove(app.watchdog_id)
    assert status == 0
    if failures:
        raise failures[0]


def test_b4a_plan_card_demo_shows_plan_and_ready_nodes() -> None:
    from aconsole.ui.views.sidebar.plan_card import PlanCard

    plan = _demo_plan()

    def check() -> None:
        card = PlanCard(on_activate=lambda _plan_id: None)
        card.set_plan(plan)

        assert isinstance(card, Gtk.Button)
        assert card.plan_id == plan.id
        assert card.title_label.get_text()
        progress = card.progress_label.get_text()
        assert str(plan.done) in progress
        assert str(plan.total) in progress
        assert card.progress_label.has_css_class("numeric")
        ready_keys = [node.key for node in plan.nodes if node.ready]
        assert ready_keys
        assert all(key in card.detail_label.get_text() for key in ready_keys)

    _in_gtk_context(check)


def test_b4a_plan_card_activation_and_placeholder() -> None:
    from aconsole.ui.views.sidebar.plan_card import PlanCard, create_plan_placeholder

    plan = _demo_plan()

    def check() -> None:
        activated: list[PlanId] = []
        card = PlanCard(on_activate=activated.append)
        card.set_plan(plan)

        card.emit("clicked")

        assert activated == [plan.id]
        placeholder = create_plan_placeholder(plan)
        assert isinstance(placeholder, Adw.StatusPage)
        assert placeholder.get_title() == plan.title
        description = placeholder.get_description()
        assert description is not None
        assert plan.goal in description

    _in_gtk_context(check)


def test_b4a_plan_card_empty_state_explains_missing_plan() -> None:
    from aconsole.ui.views.sidebar.plan_card import PlanCard

    def check() -> None:
        activated: list[PlanId] = []
        card = PlanCard(on_activate=activated.append)
        card.set_plan(None)

        assert card.plan_id is None
        assert card.title_label.get_text()
        assert card.detail_label.get_text().strip()
        card.emit("clicked")
        assert activated == []

    _in_gtk_context(check)


def test_b4a_window_plan_card_demo(running_app: str) -> None:
    """The live demo window mounts its active project's plan card in the sidebar."""
    plan = _demo_plan()

    def check(window: MainWindow) -> None:
        card = window.plan_card
        assert card.get_visible()
        assert card.plan_id == plan.id
        assert str(plan.done) in card.progress_label.get_text()
        assert str(plan.total) in card.progress_label.get_text()

    _run_window_probe(running_app, check)


def test_b4a_window_plan_card_opens_page(running_app: str) -> None:
    """Clicking the mounted card opens its plan placeholder in MainWindow."""
    plan = _demo_plan()

    def check(window: MainWindow) -> None:
        assert window.plan_placeholder is None
        window.plan_card.emit("clicked")

        page = window.plan_placeholder
        assert isinstance(page, Adw.StatusPage)
        assert page.get_title() == plan.title
        description = page.get_description()
        assert description is not None
        assert plan.goal in description

    _run_window_probe(running_app, check)
