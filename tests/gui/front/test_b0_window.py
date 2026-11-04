"""B0 window behavior on the demo core and startup failure path."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from aconsole.ui.bootstrap import run_app  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.views.app import AConsoleApp, SnapshotRequest  # noqa: E402

if TYPE_CHECKING:
    from pathlib import Path

    from aconsole.ui.views.window import MainWindow


def _walk_widgets(widget: Gtk.Widget) -> list[Gtk.Widget]:
    widgets = [widget]
    child = widget.get_first_child()
    while child is not None:
        widgets.extend(_walk_widgets(child))
        child = child.get_next_sibling()
    return widgets


def _headerbars(widget: Gtk.Widget) -> list[Adw.HeaderBar]:
    return [item for item in _walk_widgets(widget) if isinstance(item, Adw.HeaderBar)]


def _header_layout_error(headers: list[Adw.HeaderBar]) -> str | None:
    for header_index, header in enumerate(headers):
        header_layout = header.get_decoration_layout()
        if header_layout is not None:
            return f"header {header_index} overrides system layout with {header_layout!r}"
    return None


class _LayoutProbeApp(AConsoleApp):
    def __init__(self, client: CoreClient, request: SnapshotRequest, settings: Gtk.Settings, layout: str) -> None:
        super().__init__(client, request)
        self.settings = settings
        self.layout = layout
        self.layout_change_observed = False
        self.controls_layout_error: str | None = "not checked"
        self._frame_clock_handler: int | None = None

    def _capture(self) -> None:
        window = self.window
        if not self.layout_change_observed:
            self.settings.set_property("gtk-decoration-layout", self.layout)
            self.layout_change_observed = (
                self.settings.get_property("gtk-decoration-layout") == self.layout
            )
            if window is not None:
                clock = window.get_frame_clock()
                if clock is not None:
                    self._frame_clock_handler = clock.connect("after-paint", self._capture_after_paint)
                    window.queue_draw()
                    return
        if window is not None:
            headers = _headerbars(window)
            self.controls_layout_error = _header_layout_error(headers)
        super()._capture()

    def _capture_after_paint(self, clock: Gdk.FrameClock) -> None:
        if self._frame_clock_handler is not None:
            clock.disconnect(self._frame_clock_handler)
            self._frame_clock_handler = None
        self._capture()


def _assert_header_state(window: MainWindow, *, collapsed: bool) -> None:
    assert window.split.get_collapsed() is collapsed
    headers = _headerbars(window)
    assert len(headers) == 2
    main_header = window.content_header
    sidebar_header = next(header for header in headers if header is not main_header)
    # Wide: the terminal fills the right column; each system button keeps its side, in an island there.
    assert not sidebar_header.get_show_start_title_buttons()
    assert not sidebar_header.get_show_end_title_buttons()
    assert main_header.get_visible() is collapsed
    assert window.start_controls.get_side() == Gtk.PackType.START
    assert window.end_controls.get_side() == Gtk.PackType.END
    assert window.start_island.get_visible() is (not collapsed and not window.start_controls.get_empty())
    assert window.end_island.get_visible() is (not collapsed and not window.end_controls.get_empty())
    assert all(header.get_decoration_layout() is None for header in headers)


@pytest.mark.parametrize(("width", "collapsed"), [(1280, False), (700, True)])
def test_sidebar_lists_demo_tabs(
    running_app: str, tmp_path: Path, width: int, *, collapsed: bool,
) -> None:
    from aconsole.ui.bootstrap import run_app
    from aconsole.ui.client.core_client import CoreClient
    from aconsole.ui.views.app import AConsoleApp, SnapshotRequest

    request = SnapshotRequest(view="window", theme="light", width=width, height=800, out=tmp_path / "window.png")
    client = CoreClient(running_app)
    app = AConsoleApp(client, request)
    assert run_app(lambda: app, ["aconsole-test"]) == 0
    assert not app.failed
    assert request.out.stat().st_size > 0

    window = app.window
    assert window is not None
    snapshot = client.store.snapshot
    assert snapshot is not None
    expected = sorted(snapshot.tabs, key=lambda tab: tab.order)
    assert len(expected) == 5
    assert window.selection.get_n_items() == 5
    selected = [window.selection.get_item(index) for index in range(5)]
    assert all(item is not None for item in selected)
    assert [item.get_property("tab-id") for item in selected if item is not None] == [
        str(tab.id) for tab in expected
    ]
    assert [item.get_property("title") for item in selected if item is not None] == [
        tab.title for tab in expected
    ]
    assert window.split.get_collapsed() is collapsed
    assert window.split.get_show_sidebar() is (not collapsed)


def test_collapsed_sidebar_button_opens_and_closes_sidebar(
    running_app: str, tmp_path: Path,
) -> None:
    from aconsole.ui.bootstrap import run_app
    from aconsole.ui.client.core_client import CoreClient
    from aconsole.ui.views.app import AConsoleApp, SnapshotRequest

    request = SnapshotRequest(view="window", theme="light", width=700, height=800, out=tmp_path / "narrow.png")
    client = CoreClient(running_app)
    app = AConsoleApp(client, request)
    assert run_app(lambda: app, ["aconsole-test-sidebar-toggle"]) == 0
    assert not app.failed

    window = app.window
    assert window is not None
    assert window.split.get_collapsed()
    assert window.sidebar_button.get_visible()
    assert not window.split.get_show_sidebar()

    window.sidebar_button.emit("clicked")
    assert window.split.get_show_sidebar()

    window.sidebar_button.emit("clicked")
    assert not window.split.get_show_sidebar()


@pytest.mark.parametrize(
    "decoration_layout",
    [
        "close,minimize,maximize:",
        ":close,minimize,maximize",
        "close,minimize,maximize:close,minimize,maximize",
    ],
    ids=["left", "right", "both"],
)
@pytest.mark.parametrize(("width", "collapsed"), [(1280, False), (700, True)])
def test_system_title_buttons_keep_clear_header_space(
    running_app: str,
    tmp_path: Path,
    decoration_layout: str,
    width: int,
    *,
    collapsed: bool,
) -> None:
    """Keep GTK's title-button slots available on both sides at both widths."""
    settings = Gtk.Settings.get_default()
    assert settings is not None
    previous_layout = settings.get_property("gtk-decoration-layout")
    settings.set_property("gtk-decoration-layout", decoration_layout)
    try:
        request = SnapshotRequest(
            view="window",
            theme="light",
            width=width,
            height=800,
            out=tmp_path / f"window-{width}.png",
        )
        client = CoreClient(running_app)
        changed_layout = (
            ":close,minimize,maximize"
            if decoration_layout.endswith(":")
            else "close,minimize,maximize:"
        )
        app = _LayoutProbeApp(client, request, settings, changed_layout)
        assert run_app(lambda: app, ["aconsole-test-title-buttons"]) == 0
        assert not app.failed
        assert app.layout_change_observed
        assert app.controls_layout_error is None, app.controls_layout_error

        window = app.window
        assert window is not None
        _assert_header_state(window, collapsed=collapsed)

        assert request.out.is_file()
        assert request.out.stat().st_size > 0
    finally:
        settings.set_property("gtk-decoration-layout", previous_layout)


def test_startup_connection_error_says_so_and_exits_once_closed(tmp_path: Path) -> None:
    from gi.repository import GLib

    from aconsole.ui.bootstrap import run_app
    from aconsole.ui.client.core_client import CoreClient
    from aconsole.ui.views.app import AConsoleApp

    app = AConsoleApp(CoreClient(str(tmp_path / "missing-core.sock")))
    stayed: list[bool] = []

    def look_and_close() -> bool:
        if not app.failed:
            return GLib.SOURCE_CONTINUE
        window = app.startup_window
        stayed.append(window is not None and window.get_visible())
        if window is not None:
            window.close()
        return GLib.SOURCE_REMOVE

    _ = GLib.timeout_add(100, look_and_close)
    assert run_app(lambda: app, ["aconsole-test-error"]) == 0
    assert stayed == [True]
    assert app.failed
    assert app.window is None
