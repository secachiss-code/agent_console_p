"""B10 setup wizard and shared terminal preferences UI behavior."""

from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, override

import gi
import msgspec

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from aconsole.contracts.app import (  # noqa: E402
    ApplyFixParams,
    CheckResult,
    ChecksResult,
    EmptyResult,
    FixPreview,
    TerminalLaunchSpec,
)
from aconsole.contracts.settings import Settings, SettingsPatch  # noqa: E402
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.viewmodels.setup_vm import PreferencesViewModel  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabsViewModel  # noqa: E402
from aconsole.ui.views.window import MainWindow  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


_NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


class _ViewClient(CoreClient):
    def __init__(self) -> None:
        super().__init__("unused")
        self.requests: list[tuple[str, msgspec.Struct]] = []
        self.settings_result = Settings(
            rev=3,
            vault_path="/vault",
            worktrees_dir="/worktrees",
        )
        self.updated_settings = msgspec.structs.replace(
            self.settings_result,
            rev=4,
            terminal_font="Iosevka",
            terminal_font_size=14,
            scrollback_lines=40000,
            theme="dark",
            setup_completed=True,
        )
        self.checks_result = ChecksResult(checks=(_check(),))
        self.preview_result = FixPreview(
            fix_id="fix-storage",
            summary="Repair storage",
            diff="before -> after",
            commands=("repair-storage",),
            reversible=True,
            confirm_token="the-visible-confirmation-token",  # noqa: S106 -- deterministic fake token for this test fixture.
            expires_at=_NOW + timedelta(minutes=5),
        )
        self.project_result = demo_snapshot().projects[0]
        self.tab_result = demo_snapshot().tabs[0]
        self.apply_result = _check(ok=True)

    @override
    async def request[T: msgspec.Struct](
        self,
        method: str,
        params: msgspec.Struct,
        result_type: type[T],
        *,
        long: bool = False,
    ) -> T:
        _ = long
        self.requests.append((method, params))
        values: dict[str, msgspec.Struct] = {
            "settings/get": self.settings_result,
            "settings/update": self.updated_settings,
            "setup/checks": self.checks_result,
            "setup/previewFix": self.preview_result,
            "setup/applyFix": self.apply_result,
            "project/add": self.project_result,
            "project/setActive": EmptyResult(),
            "tab/open": self.tab_result,
            "tab/launchSpec": TerminalLaunchSpec(
                argv=("/usr/bin/true",),
                env={},
                cwd=tempfile.gettempdir(),
                scrollback_lines=20000,
                title="B10 test terminal",
            ),
            "tab/started": EmptyResult(),
            "tab/exited": EmptyResult(),
            "tab/focus": EmptyResult(),
        }
        return msgspec.convert(values[method], type=result_type)


def _check(*, ok: bool = False) -> CheckResult:
    return CheckResult(
        id="storage",
        title="Storage",
        ok=ok,
        severity="error" if not ok else "info",
        detail="Storage needs attention" if not ok else None,
        fix_id=None if ok else "fix-storage",
        requires_confirmation=not ok,
    )


class _ViewTestApp(Adw.Application):
    def __init__(self, scenario: Callable[[_ViewTestApp], Awaitable[None]]) -> None:
        super().__init__(
            application_id="io.github.aconsole.B10ViewsTest",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.scenario = scenario
        self.client = _ViewClient()
        self.window: Gtk.Window | None = None
        self.error: Exception | None = None

    @override
    def do_activate(self) -> None:
        self.hold()
        _ = spawn_task(self._run_scenario())

    async def _run_scenario(self) -> None:
        try:
            await asyncio.wait_for(self.scenario(self), timeout=15.0)
        except Exception as error:  # noqa: BLE001 -- surface asynchronous GTK failures to pytest.
            self.error = error
        finally:
            if self.window is not None:
                self.window.close()
            self.release()
            self.quit()


def _run_ui(scenario: Callable[[_ViewTestApp], Awaitable[None]]) -> None:
    app = _ViewTestApp(scenario)
    assert run_app(lambda: app, ["aconsole-b10-views-test"]) == 0
    assert app.error is None, repr(app.error)


async def _until(predicate: Callable[[], bool]) -> None:
    async def poll() -> None:
        while not predicate():  # noqa: ASYNC110 -- GTK mapping and RPC completion are event-driven.
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout=5.0)


def test_b10_wizard_can_skip_read_only_checks_and_persist_completion() -> None:
    from aconsole.ui.views.setup.wizard import SetupWizard

    async def scenario(app: _ViewTestApp) -> None:
        snapshot = demo_snapshot()
        app.client.store.apply_snapshot(snapshot, 0)
        window = Adw.ApplicationWindow(application=app)
        app.window = window
        window.present()
        preferences = PreferencesViewModel(app.client)
        await preferences.load()
        wizard = SetupWizard(app.client, preferences, on_tab=lambda _tab_id: None)
        wizard.open(window)
        await _until(lambda: not wizard.loading)
        assert any(method == "setup/checks" for method, _params in app.client.requests)
        assert all(
            method not in {"setup/previewFix", "setup/applyFix"}
            for method, _params in app.client.requests
        )

        wizard.skip()
        await _until(
            lambda: any(method == "settings/update" for method, _params in app.client.requests)
        )
        assert wizard.step == 4
        assert preferences.settings is not None
        assert preferences.settings.setup_completed
        updates = [
            params
            for method, params in app.client.requests
            if method == "settings/update"
        ]
        assert len(updates) == 1
        assert updates[0].patch == SettingsPatch(setup_completed=True)
        assert updates[0].rev == 3
        assert all(
            method != "setup/applyFix" for method, _params in app.client.requests
        )

    _run_ui(scenario)


def test_b10_wizard_apply_button_sends_exact_visible_preview_token() -> None:
    from aconsole.ui.views.setup.wizard import SetupWizard

    async def scenario(app: _ViewTestApp) -> None:
        preferences = PreferencesViewModel(app.client)
        await preferences.load()
        window = Adw.ApplicationWindow(application=app)
        app.window = window
        window.present()
        wizard = SetupWizard(app.client, preferences, on_tab=lambda _tab_id: None)
        wizard.open(window)
        await _until(lambda: not wizard.loading)
        wizard.next_step()
        wizard.next_step()
        await _until(lambda: wizard.viewmodel.preview is not None)

        preview = wizard.viewmodel.preview
        assert preview is not None
        assert not any(
            method == "setup/applyFix" for method, _params in app.client.requests
        )
        wizard.apply_button.emit("clicked")
        await _until(
            lambda: any(method == "setup/applyFix" for method, _params in app.client.requests)
        )

        apply_params = next(
            params
            for method, params in app.client.requests
            if method == "setup/applyFix"
        )
        assert apply_params == ApplyFixParams(
            fix_id=preview.fix_id,
            confirm_token=preview.confirm_token,
        )
        assert sum(
            method == "setup/applyFix" for method, _params in app.client.requests
        ) == 1

    _run_ui(scenario)


def test_b10_preferences_save_updates_two_retained_terminals_without_restart() -> None:
    from aconsole.ui.views.setup.preferences import PreferencesDialog

    async def scenario(app: _ViewTestApp) -> None:
        snapshot = demo_snapshot()
        app.client.store.apply_snapshot(snapshot, 0)
        window = MainWindow(app, TabsViewModel(app.client))
        app.window = window
        window.present()
        await _until(lambda: bool(window.terminal_pages))
        first = next(iter(window.terminal_pages.values()))
        first_index = window.selection.get_selected()
        second_index = (first_index + 1) % window.selection.get_n_items()
        window.selection.set_selected(second_index)
        await _until(lambda: len(window.terminal_pages) == 2)
        second = next(page for page in window.terminal_pages.values() if page is not first)
        launch_count = sum(
            method == "tab/launchSpec" for method, _params in app.client.requests
        )
        workers = (first.worker, second.worker)

        assert window.preferences_dialog is None
        window.app_vm.dispatch("settings")
        dialog = window.preferences_dialog
        assert isinstance(dialog, PreferencesDialog)
        dialog.font_entry.set_text("Iosevka")
        dialog.font_size.set_value(14)
        dialog.scrollback.set_value(40000)
        dialog.theme.set_selected(2)
        dialog.save_button.emit("clicked")
        await _until(
            lambda: any(method == "settings/update" for method, _params in app.client.requests)
        )
        await _until(
            lambda: all(
                page.terminal.get_scrollback_lines() == 40000
                for page in (first, second)
            )
        )

        assert window.preferences_vm.settings == app.client.updated_settings
        for page, worker in zip((first, second), workers, strict=True):
            assert page.worker is worker
            assert page.terminal.get_font().get_family() == "Iosevka"
        assert sum(
            method == "tab/launchSpec" for method, _params in app.client.requests
        ) == launch_count
        update_params = next(
            params
            for method, params in app.client.requests
            if method == "settings/update"
        )
        assert update_params.patch == SettingsPatch(
            terminal_font="Iosevka",
            terminal_font_size=14,
            scrollback_lines=40000,
            theme="dark",
        )

    _run_ui(scenario)


def test_b10_wizard_refreshes_agents_live_and_projects_when_reopened() -> None:
    from aconsole.contracts.common import ProjectId
    from aconsole.contracts.events import AgentsChanged, ProjectsChanged
    from aconsole.contracts.ipc import EventEnvelope
    from aconsole.ui.views.setup.wizard import SetupWizard

    async def scenario(app: _ViewTestApp) -> None:
        snapshot = demo_snapshot()
        available_agents = snapshot.agents
        snapshot = msgspec.structs.replace(
            snapshot,
            agents=tuple(
                msgspec.structs.replace(agent, available=False)
                for agent in available_agents
            ),
        )
        app.client.store.apply_snapshot(snapshot, 0)
        window = Adw.ApplicationWindow(application=app)
        app.window = window
        window.present()
        preferences = PreferencesViewModel(app.client)
        await preferences.load()
        wizard = SetupWizard(app.client, preferences, on_tab=lambda _tab_id: None)
        wizard.open(window)
        assert wizard.worker is not None
        await asyncio.wait_for(wizard.worker, timeout=3.0)

        agents = wizard.agent_dropdown.get_model()
        assert agents is not None
        assert agents.get_n_items() == 0
        assert not wizard.agent_dropdown.get_sensitive()
        assert not wizard.first_task_button.get_sensitive()

        assert app.client.store.apply(
            EventEnvelope(
                seq=1,
                at=_NOW,
                event=AgentsChanged(agents=available_agents),
            )
        )
        agents = wizard.agent_dropdown.get_model()
        assert agents is not None
        assert agents.get_n_items() == len(available_agents)
        assert wizard.agent_dropdown.get_sensitive()
        assert wizard.first_task_button.get_sensitive()

        wizard.close()
        await asyncio.sleep(0.05)
        added_project = msgspec.structs.replace(
            snapshot.projects[0],
            id=ProjectId("01J9Z5W6FX1M2A3B4C5D6E7F8G"),
            name="New setup project",
            root="/projects/setup-new",
        )
        assert app.client.store.apply(
            EventEnvelope(
                seq=2,
                at=_NOW,
                event=ProjectsChanged(
                    projects=(*snapshot.projects, added_project),
                    active_project=added_project.id,
                ),
            )
        )

        wizard.open(window)
        assert wizard.worker is not None
        await asyncio.wait_for(wizard.worker, timeout=3.0)
        projects = wizard.project_dropdown.get_model()
        assert projects is not None
        project_names = [
            projects.get_string(index) for index in range(projects.get_n_items())
        ]
        assert "New setup project" in project_names
        assert wizard.agent_dropdown.get_sensitive()

    _run_ui(scenario)
