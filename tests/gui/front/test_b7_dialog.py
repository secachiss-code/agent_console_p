"""B7 palette dialog navigation and command execution."""

from __future__ import annotations

import asyncio
import tempfile
from typing import TYPE_CHECKING, override

import gi
import msgspec

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk  # noqa: E402

from aconsole.contracts.app import (  # noqa: E402
    CommandResult,
    PaletteArgument,
    PaletteExecuteParams,
    PaletteItem,
    PaletteItemsResult,
    PaletteKind,
    PaletteQuery,
    ProjectId,
    TerminalLaunchSpec,
)
from aconsole.contracts.common import TabId  # noqa: E402
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.viewmodels.tabs_vm import TabItem, TabsViewModel  # noqa: E402
from aconsole.ui.views.window import MainWindow  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aconsole.ui.views.palette.dialog import PaletteDialog


_TIMEOUT_SECONDS = 8.0


class _PaletteClient(CoreClient):
    def __init__(self, items: tuple[PaletteItem, ...]) -> None:
        super().__init__("unused")
        self.items = items
        self.requests: list[tuple[str, msgspec.Struct]] = []
        self.execute_result = CommandResult(ok=True, toast="Command completed")

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
        if method == "palette/items":
            return msgspec.convert(PaletteItemsResult(items=self.items), type=result_type)
        if method == "palette/execute":
            return msgspec.convert(self.execute_result, type=result_type)
        if method == "tab/launchSpec":
            spec = TerminalLaunchSpec(
                argv=("/usr/bin/true",),
                env={},
                cwd=tempfile.gettempdir(),
                scrollback_lines=0,
                title="palette test terminal",
            )
            return msgspec.convert(spec, type=result_type)
        return result_type()


class _PaletteTestApp(Adw.Application):
    def __init__(self, scenario: Callable[[_PaletteTestApp], Awaitable[None]]) -> None:
        super().__init__(
            application_id="io.github.aconsole.B7DialogTest",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.scenario = scenario
        self.client = _PaletteClient(_items())
        self.window: Gtk.Window | None = None
        self.error: Exception | None = None

    @override
    def do_activate(self) -> None:
        self.hold()
        _ = spawn_task(self._run_scenario())

    async def _run_scenario(self) -> None:
        try:
            await asyncio.wait_for(self.scenario(self), timeout=_TIMEOUT_SECONDS)
        except Exception as error:  # noqa: BLE001 -- report async GTK failures to pytest.
            self.error = error
        finally:
            if self.window is not None:
                self.window.close()
            self.release()
            self.quit()


def _item(
    item_id: str,
    title: str,
    *,
    aliases: tuple[str, ...] = (),
    enabled: bool = True,
    argument: PaletteArgument | None = None,
) -> PaletteItem:
    return PaletteItem(
        id=item_id,
        kind=PaletteKind.COMMAND,
        title=title,
        subtitle="Palette test command",
        keywords=("switch",),
        aliases=aliases,
        score_hint=1.0,
        enabled=enabled,
        disabled_reason=None if enabled else "Unavailable in this context",
        argument=argument,
        shortcut=None,
    )


def _items() -> tuple[PaletteItem, ...]:
    argument = PaletteArgument(
        label="Agent",
        choices=(("claude", "Claude"), ("codex", "Codex")),
    )
    return (
        _item("switch-agent", "Switch agent", aliases=("swi",), argument=argument),
        _item(
            "disabled-switch",
            "Switch unavailable",
            aliases=("swi",),
            enabled=False,
        ),
        _item("open-settings", "Open settings", aliases=("set",)),
    )


def _context() -> PaletteQuery:
    snapshot = demo_snapshot()
    assert snapshot.active_project is not None
    return PaletteQuery(
        tab_id=TabId(str(snapshot.tabs[0].id)),
        project_id=ProjectId(str(snapshot.active_project)),
    )


def _run_ui(scenario: Callable[[_PaletteTestApp], Awaitable[None]]) -> None:
    app = _PaletteTestApp(scenario)
    assert run_app(lambda: app, ["aconsole-b7-dialog-test"]) == 0
    assert app.error is None, repr(app.error)


async def _until(predicate: Callable[[], bool]) -> None:
    async def poll() -> None:
        while not predicate():  # noqa: ASYNC110 -- list-box binding and RPC completion are event-driven.
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout=5.0)


def _labels(widget: Gtk.Widget) -> list[str]:
    texts = [widget.get_text()] if isinstance(widget, Gtk.Label) else []
    child = widget.get_first_child()
    while child is not None:
        texts.extend(_labels(child))
        child = child.get_next_sibling()
    return texts


async def _open_palette_dialog(
    app: _PaletteTestApp, on_result: Callable[[CommandResult], None]
) -> tuple[Adw.ApplicationWindow, PaletteDialog, PaletteQuery]:
    snapshot = demo_snapshot()
    app.client.store.apply_snapshot(snapshot, 0)
    window = Adw.ApplicationWindow(application=app)
    app.window = window
    window.present()
    from aconsole.ui.views.palette.dialog import PaletteDialog as Dialog

    dialog = Dialog(app.client, on_result)
    context = _context()
    dialog.open(context, window)
    assert dialog.worker is not None
    await asyncio.wait_for(dialog.worker, timeout=3.0)
    return window, dialog, context


async def _search_and_reject_disabled(
    dialog: PaletteDialog, app: _PaletteTestApp
) -> None:
    for query in ("s", "sw", "swi"):
        dialog.search_entry.set_text(query)
    assert [method for method, _params in app.client.requests] == ["palette/items"]
    assert dialog.viewmodel.query == "swi"
    assert len(dialog.viewmodel.items) == 2
    assert any(
        "Unavailable in this context" in text
        for text in _labels(dialog.results_list)
    )

    disabled_row = dialog.results_list.get_row_at_index(1)
    assert disabled_row is not None
    dialog.results_list.select_row(disabled_row)
    dialog.activate_selected()
    await asyncio.sleep(0.05)
    assert [method for method, _params in app.client.requests] == ["palette/items"]

    enabled_row = dialog.results_list.get_row_at_index(0)
    assert enabled_row is not None
    dialog.results_list.select_row(enabled_row)
    dialog.activate_selected()
    assert dialog.viewmodel.selected is not None
    assert dialog.viewmodel.selected.id == "switch-agent"
    assert dialog.argument_list.get_row_at_index(1) is not None
    dialog.go_back()
    assert dialog.search_entry.get_text() == "swi"
    assert dialog.viewmodel.query == "swi"
    assert dialog.viewmodel.items


async def _execute_codex_choice(
    dialog: PaletteDialog,
    app: _PaletteTestApp,
    context: PaletteQuery,
    results: list[CommandResult],
) -> None:
    dialog.results_list.select_row(dialog.results_list.get_row_at_index(0))
    dialog.activate_selected()
    codex_row = dialog.argument_list.get_row_at_index(1)
    assert codex_row is not None
    dialog.argument_list.select_row(codex_row)
    dialog.activate_argument()
    await _until(
        lambda: any(method == "palette/execute" for method, _params in app.client.requests)
    )
    execute_calls = [
        params
        for method, params in app.client.requests
        if method == "palette/execute"
    ]
    assert execute_calls == [
        PaletteExecuteParams(
            item_id="switch-agent",
            tab_id=context.tab_id,
            project_id=context.project_id,
            argument="codex",
        )
    ]
    await _until(lambda: bool(results))
    assert results == [app.client.execute_result]


async def _reopen_after_success(
    dialog: PaletteDialog,
    app: _PaletteTestApp,
    context: PaletteQuery,
    window: Adw.ApplicationWindow,
) -> None:
    await asyncio.sleep(0.05)
    assert not dialog.viewmodel.opened
    dialog.open(context, window)
    assert dialog.stack.get_sensitive()
    assert dialog.back_button.get_sensitive()
    await _until(
        lambda: sum(
            method == "palette/items" for method, _params in app.client.requests
        )
        == 2
    )
    assert dialog.worker is not None
    await asyncio.wait_for(dialog.worker, timeout=3.0)
    assert dialog.viewmodel.opened
    assert dialog.stack.get_sensitive()


def test_b7_palette_search_is_local_and_executes_selected_argument() -> None:
    async def scenario(app: _PaletteTestApp) -> None:
        results: list[CommandResult] = []
        window, dialog, context = await _open_palette_dialog(app, results.append)
        assert [method for method, _params in app.client.requests] == ["palette/items"]
        await _search_and_reject_disabled(dialog, app)
        await _execute_codex_choice(dialog, app, context, results)
        await _reopen_after_success(dialog, app, context, window)
        dialog.close()

    _run_ui(scenario)


def test_b7_palette_row_activation_uses_activated_row() -> None:
    from aconsole.ui.views.palette.dialog import PaletteDialog

    async def scenario(app: _PaletteTestApp) -> None:
        first, _disabled, _settings = app.client.items
        choice = first.argument
        assert choice is not None
        workspace = _item(
            "switch-workspace",
            "Switch workspace",
            aliases=("swi",),
            argument=choice,
        )
        app.client.items = (first, workspace)
        app.client.store.apply_snapshot(demo_snapshot(), 0)
        window = Adw.ApplicationWindow(application=app)
        app.window = window
        window.present()
        dialog = PaletteDialog(app.client, lambda _result: None)
        dialog.open(_context(), window)
        assert dialog.worker is not None
        await asyncio.wait_for(dialog.worker, timeout=3.0)
        dialog.search_entry.set_text("swi")

        selected = dialog.results_list.get_selected_row()
        target = dialog.results_list.get_row_at_index(1)
        assert selected is not None
        assert selected.get_index() == 0
        assert target is not None
        dialog.results_list.emit("row-activated", target)

        assert dialog.viewmodel.selected is not None
        assert dialog.viewmodel.selected.id == "switch-workspace"
        assert dialog.stack.get_visible_child_name() == "arguments"
        dialog.close()

    _run_ui(scenario)


def test_b7_main_window_opens_palette_lazily_with_selected_context() -> None:
    async def scenario(app: _PaletteTestApp) -> None:
        snapshot = demo_snapshot()
        app.client.store.apply_snapshot(snapshot, 0)
        window = MainWindow(app, TabsViewModel(app.client))
        app.window = window
        window.present()
        assert window.palette_dialog is None

        window.app_vm.dispatch("palette")
        dialog = window.palette_dialog
        assert dialog is not None
        await _until(lambda: not dialog.viewmodel.loading)
        selected = window.selection.get_selected_item()
        assert isinstance(selected, TabItem)
        selected_tab_id = str(selected.tab_id)
        assert snapshot.active_project is not None

        item_request = next(
            params
            for method, params in app.client.requests
            if method == "palette/items"
        )
        assert item_request == PaletteQuery(
            tab_id=TabId(selected_tab_id),
            project_id=snapshot.active_project,
        )
        dialog.close()

    _run_ui(scenario)
