"""CLI for hostrun ui-snapshot. The spike writes one PNG and exits."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from aconsole.ui.spike import run_spike

if TYPE_CHECKING:
    from aconsole.ui.client.core_client import CoreClient
    from aconsole.ui.views.app import AConsoleApp, SnapshotRequest


class _SnapshotArguments(argparse.Namespace):
    scenario: str = "demo"
    view: str = "window"
    theme: str = "light"
    size: str = "1280x800"
    out: Path = Path("docs/ui/B0/window.png")


def main(argv: list[str]) -> int:
    if argv == ["--spike"]:
        destination = Path("docs/ui/spike.png")
        destination.parent.mkdir(parents=True, exist_ok=True)
        run_spike(snapshot=destination, autoquit=True)
        if not destination.is_file() or destination.stat().st_size == 0:
            sys.stdout.write("snapshot missing\n")
            return 1
        sys.stdout.write(f"wrote {destination}\n")
        return 0
    from aconsole.__main__ import run_ui  # noqa: PLC0415

    parser = argparse.ArgumentParser()
    _ = parser.add_argument("--scenario", required=True, choices=("demo", "empty", "stress", "errors"))
    _ = parser.add_argument("--view", required=True, choices=("window", "tabs", "plan-graph", "quota-dialog"))
    _ = parser.add_argument("--theme", required=True, choices=("light", "dark"))
    _ = parser.add_argument("--size", required=True)
    _ = parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv, namespace=_SnapshotArguments())
    result = run_ui([
        "--fake", args.scenario, "--snapshot-view", args.view, "--theme", args.theme,
        "--size", args.size, "--out", str(args.out),
    ], app_factory=_preview_app if args.view in {"plan-graph", "quota-dialog"} else None)
    if result or not args.out.is_file() or args.out.stat().st_size == 0:
        _ = sys.stderr.write("snapshot missing or application failed\n")
        return result or 1
    _ = sys.stdout.write(f"wrote {args.out}\n")
    return 0


def _preview_app(client: CoreClient, request: SnapshotRequest | None, scenario: str | None) -> AConsoleApp:
    """Use an explicit app factory for native popover/dialog snapshot targets."""
    from gi.repository import Gdk, GLib, Gtk  # noqa: PLC0415

    from aconsole.ui.bootstrap import spawn_task  # noqa: PLC0415
    from aconsole.ui.snapshot import capture_window  # noqa: PLC0415
    from aconsole.ui.views.app import AConsoleApp  # noqa: PLC0415

    class _PreviewApp(AConsoleApp):
        _paint_handle: int | None = None
        _paint_clock: Gdk.FrameClock | None = None
        _preview_watchdog: int | None = None

        def _capture(self) -> None:
            window = self.window
            snapshot = self.snapshot_request
            if window is None or snapshot is None:
                super()._capture()
                return
            if snapshot.view == "plan-graph":
                window.plan_card.show_graph()
            else:
                _ = window.quota_card.open_details(window)
            _ = GLib.timeout_add(450, self._arm_preview)

        def _preview_target(self) -> Gtk.Widget | None:
            window = self.window
            snapshot = self.snapshot_request
            if window is None or snapshot is None:
                return None
            if snapshot.view == "quota-dialog":
                return window.quota_card.details_dialog
            return window.plan_card.graph_popover

        def _arm_preview(self) -> bool:
            target = self._preview_target()
            self._paint_clock = target.get_frame_clock() if target is not None else None
            if target is None or self._paint_clock is None:
                return self._preview_timed_out()
            self._paint_handle = self._paint_clock.connect("after-paint", self._preview_painted)
            self._preview_watchdog = GLib.timeout_add(5000, self._preview_timed_out)
            target.queue_draw()
            return GLib.SOURCE_REMOVE

        def _preview_painted(self, clock: Gdk.FrameClock) -> None:
            if self._paint_handle is not None:
                clock.disconnect(self._paint_handle)
                self._paint_handle = None
            if self._preview_watchdog is not None:
                _ = GLib.source_remove(self._preview_watchdog)
                self._preview_watchdog = None
            _ = self._capture_preview()

        def _preview_timed_out(self) -> bool:
            if self._paint_handle is not None and self._paint_clock is not None:
                self._paint_clock.disconnect(self._paint_handle)
                self._paint_handle = None
            self.failed = True
            _ = sys.stderr.write("preview did not produce a frame\n")
            _ = spawn_task(self._finish())
            return GLib.SOURCE_REMOVE

        def _capture_preview(self) -> bool:
            snapshot = self.snapshot_request
            target = self._preview_target()
            if snapshot is None or target is None:
                return self._preview_timed_out()
            try:
                snapshot.out.parent.mkdir(parents=True, exist_ok=True)
                capture_window(target, snapshot.out)
            except Exception as error:  # noqa: BLE001 -- a failed artifact must produce a failing CLI exit
                self.failed = True
                _ = sys.stderr.write(f"preview snapshot failed: {error}\n")
            _ = spawn_task(self._finish())
            return GLib.SOURCE_REMOVE

    return _PreviewApp(client, request, scenario=scenario)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
