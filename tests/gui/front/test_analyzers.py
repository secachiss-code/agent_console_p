"""Project analyzer management stays on the UI/core method boundary."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, override

import gi
import msgspec

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio  # noqa: E402

from aconsole.contracts.app import (  # noqa: E402
    AnalyzerEnableParams,
    AnalyzerItem,
    AnalyzerListParams,
    AnalyzerListResult,
    AnalyzerManageParams,
    ProjectIndexItem,
    ProjectIndexParams,
    ProjectIndexView,
)
from aconsole.contracts.common import ProjectId, TabId  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.views.sidebar.analyzers import AnalyzerAction, AnalyzerManagerDialog  # noqa: E402
from aconsole.ui.views.sidebar.project_index import ProjectIndexPage  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

class _Client(CoreClient):
    def __init__(self) -> None:
        super().__init__("unused")
        self.requests: list[tuple[str, msgspec.Struct]] = []
        self.items: dict[str, AnalyzerItem] = {
            "ruff": AnalyzerItem(
                id="ruff", name="Ruff", version="0.9.0", installed_version=None,
                state="available", detail="Python linter",
            ),
            "mypy": AnalyzerItem(
                id="mypy", name="Mypy", version="1.0", installed_version="1.0",
                state="installed", detail="",
            ),
            "eslint": AnalyzerItem(
                id="eslint", name="ESLint", version="2.0", installed_version="1.0",
                state="update_available", detail="JavaScript linter",
            ),
            "clang": AnalyzerItem(
                id="clang", name="Clang", version="1.0", installed_version=None,
                state="unsupported", detail="Unavailable on this platform",
            ),
            "pyright": AnalyzerItem(
                id="pyright", name="Pyright", version="1.0", installed_version="1.0",
                state="ready", detail="",
            ),
            "probe": AnalyzerItem(
                id="probe", name="Probe", version="1.0", installed_version="1.0",
                state="installed", detail="",
            ),
        }

    @override
    async def request[T: msgspec.Struct](
        self, method: str, params: msgspec.Struct, result_type: type[T],
    ) -> T:
        self.requests.append((method, params))
        if method == "project/index":
            value: msgspec.Struct = ProjectIndexView(
                revision="rev-1",
                state="complete",
                items=(ProjectIndexItem(id="src", name="src", path="src", kind="directory"),),
            )
        elif method == "analyzer/list":
            assert isinstance(params, AnalyzerListParams)
            value = AnalyzerListResult(items=tuple(self.items.values()))
        elif method == "analyzer/setEnabled":
            assert isinstance(params, AnalyzerEnableParams)
            value = AnalyzerListResult(items=tuple(self.items.values()), enabled=params.enabled)
        else:
            assert method == "analyzer/manage"
            assert isinstance(params, AnalyzerManageParams)
            item = self.items[params.id]
            if params.id == "probe":
                item = msgspec.structs.replace(item, state="error", detail="Analyzer probe failed: invalid config")
            elif params.action in {"install", "update"}:
                item = msgspec.structs.replace(item, installed_version=item.version, state="installed")
            elif params.action == "remove":
                item = msgspec.structs.replace(item, installed_version=None, state="available")
            else:
                item = msgspec.structs.replace(item, state="ready")
            self.items[params.id] = item
            value = item
        return msgspec.convert(value, type=result_type)


class _App(Adw.Application):
    def __init__(self, scenario: Callable[[_App], Awaitable[None]]) -> None:
        super().__init__(application_id="io.github.aconsole.AnalyzerUiTest", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.scenario: Callable[[_App], Awaitable[None]] = scenario
        self.client: _Client = _Client()
        self.error: BaseException | None = None
        self.page: ProjectIndexPage | None = None
        self.window: Adw.ApplicationWindow | None = None

    @override
    def do_activate(self) -> None:
        self.hold()
        _ = spawn_task(self._run())

    async def _run(self) -> None:
        window = Adw.ApplicationWindow(application=self)
        params = ProjectIndexParams(project_id=ProjectId("project-1"), tab_id=TabId("tab-1"))
        page = ProjectIndexPage(self.client, params, lambda: None)
        self.page = page
        self.window = window
        window.set_content(page)
        window.present()
        try:
            await asyncio.wait_for(self.scenario(self), timeout=5)
        except BaseException as error:  # noqa: BLE001 -- preserve GTK and async failures for pytest
            self.error = error
        finally:
            if page.analyzer_dialog is not None:
                _ = page.analyzer_dialog.close()
            window.close()
            self.release()
            self.quit()


def _run_ui(scenario: Callable[[_App], Awaitable[None]]) -> None:
    app = _App(scenario)
    assert run_app(lambda: app, ["aconsole-analyzer-ui-test"]) == 0
    assert app.error is None, repr(app.error)


async def _until(check: Callable[[], bool]) -> None:
    for _ in range(100):
        if check():
            return
        await asyncio.sleep(0.01)
    message = "analyzer UI request did not settle"
    raise AssertionError(message)


def test_project_index_opens_manager_and_routes_all_actions() -> None:
    async def scenario(app: _App) -> None:
        client = app.client
        await _until(lambda: any(method == "project/index" for method, _ in client.requests))
        page = next(
            request
            for method, request in client.requests
            if method == "project/index"
        )
        assert isinstance(page, ProjectIndexParams)
        # The button builds a dialog using the same project and selected tab context.
        project_page = app.page
        assert project_page is not None
        assert project_page.analyzers_button.get_sensitive()
        project_page.analyzers_button.emit("clicked")
        dialog = project_page.analyzer_dialog
        assert isinstance(dialog, AnalyzerManagerDialog)
        await _until(lambda: any(method == "analyzer/list" for method, _ in client.requests))
        listed = next(params for method, params in client.requests if method == "analyzer/list")
        assert listed == AnalyzerListParams(project_id=ProjectId("project-1"), tab_id=TabId("tab-1"))
        assert not dialog.status.get_visible()
        switch = dialog._enable_switch  # pyright: ignore[reportPrivateUsage]
        for enabled in (False, True):
            switch.set_active(enabled)
            await _until(switch.get_sensitive)
            assert switch.get_active() == enabled
            assert client.requests[-1] == (
                "analyzer/setEnabled",
                AnalyzerEnableParams(project_id=ProjectId("project-1"), tab_id=TabId("tab-1"), enabled=enabled),
            )
        assert dialog._items["ruff"].name == "Ruff"  # pyright: ignore[reportPrivateUsage]
        assert dialog._controls["ruff"]["install"].get_sensitive()  # pyright: ignore[reportPrivateUsage]
        assert not dialog._controls["ruff"]["check"].get_sensitive()  # pyright: ignore[reportPrivateUsage]
        assert not dialog._controls["ruff"]["update"].get_sensitive()  # pyright: ignore[reportPrivateUsage]
        assert not dialog._controls["clang"]["install"].get_sensitive()  # pyright: ignore[reportPrivateUsage]
        assert not dialog._controls["mypy"]["install"].get_sensitive()  # pyright: ignore[reportPrivateUsage]
        assert not dialog._controls["mypy"]["update"].get_sensitive()  # pyright: ignore[reportPrivateUsage]
        assert dialog._controls["eslint"]["update"].get_sensitive()  # pyright: ignore[reportPrivateUsage]
        assert dialog._controls["mypy"]["remove"].get_sensitive()  # pyright: ignore[reportPrivateUsage]
        assert dialog._controls["pyright"]["check"].get_sensitive()  # pyright: ignore[reportPrivateUsage]

        actions: tuple[tuple[str, AnalyzerAction], ...] = (
            ("ruff", "install"), ("eslint", "update"), ("mypy", "remove"), ("pyright", "check"),
        )
        for expected_count, (analyzer_id, action) in enumerate(actions, start=1):
            dialog._controls[analyzer_id][action].emit("clicked")  # pyright: ignore[reportPrivateUsage]
            if expected_count == 1:
                assert not any(
                    control.get_sensitive()
                    for controls in dialog._controls.values()  # pyright: ignore[reportPrivateUsage]
                    for control in controls.values()
                )
                dialog._controls["eslint"]["update"].emit("clicked")  # pyright: ignore[reportPrivateUsage]
            await _until(
                lambda expected_count=expected_count: len(
                    [1 for method, _ in client.requests if method == "analyzer/manage"]
                ) >= expected_count
            )
        managed = [params for method, params in client.requests if method == "analyzer/manage"]
        typed = [params for params in managed if isinstance(params, AnalyzerManageParams)]
        assert [(params.id, params.action) for params in typed] == list(actions)

        dialog._controls["probe"]["check"].emit("clicked")  # pyright: ignore[reportPrivateUsage]
        await _until(lambda: len([1 for method, _ in client.requests if method == "analyzer/manage"]) == 5)
        assert dialog.status.get_text() == "Analyzer probe failed: invalid config"
        assert dialog.status.has_css_class("error")
        assert "Updated" not in dialog.status.get_text()

    _run_ui(scenario)


def test_analyzer_calls_use_the_core_contract_long_timeout() -> None:
    client = CoreClient("unused", timeout_s=3.0)
    assert client.timeout_for("analyzer/list") > 3.0
    assert client.timeout_for("analyzer/manage") > 3.0


def test_python_preset_installs_both_missing_analyzers() -> None:
    async def scenario(app: _App) -> None:
        app.client.items = {
            key: AnalyzerItem(id=key, name=key.title(), version="1.0") for key in ("ruff", "pylint")
        }
        page = app.page
        assert page is not None
        page.analyzers_button.emit("clicked")
        dialog = page.analyzer_dialog
        assert dialog is not None
        preset = dialog._preset_install  # pyright: ignore[reportPrivateUsage]
        await _until(preset.get_sensitive)
        preset.emit("clicked")
        await _until(lambda: all(item.installed_version for item in app.client.items.values()))
        calls = [params for method, params in app.client.requests if method == "analyzer/manage"]
        assert [params.id for params in calls if isinstance(params, AnalyzerManageParams)] == ["ruff", "pylint"]
        assert not preset.get_sensitive()

    _run_ui(scenario)
