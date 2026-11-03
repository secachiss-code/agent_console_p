"""``python -m aconsole``."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from aconsole.ui.spike import run_spike

if TYPE_CHECKING:
    from collections.abc import Callable

    from aconsole.ui.client.core_client import CoreClient
    from aconsole.ui.views.app import AConsoleApp, SnapshotRequest

_IPC_P99_LIMIT_MS = 5.0
_SCENARIOS = ("demo", "empty", "stress", "errors", "waiting", "quota-critical", "approval-expired")


class _UiArguments(argparse.Namespace):
    fake: str | None = None
    socket: str | None = None
    snapshot_view: str | None = None
    theme: Literal["light", "dark"] = "light"
    size: str = "1280x800"
    out: Path | None = None


def main(argv: list[str]) -> int:
    if "--spike" in argv:
        p99 = run_spike(snapshot=None, autoquit=not sys.stdin.isatty())
        if p99 < 0:
            return 1
        if p99 >= _IPC_P99_LIMIT_MS:
            _ = sys.stdout.write("ipc p99 is 5 ms or more\n")
            return 1
        return 0
    return run_ui(argv)


def run_ui(
    argv: list[str],
    *,
    app_factory: Callable[[CoreClient, SnapshotRequest | None, str | None], AConsoleApp] | None = None,
) -> int:
    """No arguments: the real core, started if needed. `--fake` is an isolated daemon, `--socket` a running one."""
    from aconsole.ui.bootstrap import run_app  # noqa: PLC0415
    from aconsole.ui.client.core_client import CoreClient  # noqa: PLC0415
    from aconsole.ui.client.daemon import core_command, core_socket  # noqa: PLC0415
    from aconsole.ui.views.app import AConsoleApp, SnapshotRequest  # noqa: PLC0415

    parser = argparse.ArgumentParser()
    _ = parser.add_argument("--fake", "--fake-core", choices=_SCENARIOS)
    _ = parser.add_argument("--socket")
    _ = parser.add_argument(
        "--snapshot-view",
        choices=("window", "tabs", "plan-graph", "review", "quota-dialog", "palette", "setup", "prefs"),
    )
    _ = parser.add_argument("--theme", choices=("light", "dark"), default="light")
    _ = parser.add_argument("--size", default="1280x800")
    _ = parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv, namespace=_UiArguments())
    try:
        width, height = (int(part) for part in args.size.split("x"))
    except ValueError:
        parser.error("size must be WIDTHxHEIGHT")
    if width <= 0 or height <= 0:
        parser.error("size must be positive")
    if args.snapshot_view and args.out is None:
        parser.error("snapshot requires --out")
    snapshot = None
    if args.snapshot_view and args.out is not None:
        snapshot = SnapshotRequest(
            view=args.snapshot_view, theme=args.theme, width=width, height=height, out=args.out,
        )
    real = not args.fake and not args.socket
    with tempfile.TemporaryDirectory(prefix="aconsole-ui-") as directory:
        path = core_socket() if real else args.socket or str(Path(directory) / "core.sock")
        client = CoreClient(path)
        if app_factory is not None:
            app = app_factory(client, snapshot, args.fake)
        else:
            command = core_command() if real else None
            app = AConsoleApp(client, snapshot, scenario=args.fake, core_command=command)
        result = run_app(lambda: app, ["aconsole"])
    return 1 if app.failed else result


def console() -> None:
    raise SystemExit(main(sys.argv[1:]))


if __name__ == "__main__":
    console()
