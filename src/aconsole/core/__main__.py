"""`aconsoled --serve` and `python -m aconsole.core --fake SCENARIO [--socket PATH]`."""

from __future__ import annotations

import asyncio
import signal
import sys
import threading
from typing import TYPE_CHECKING

from aconsole.core.bootstrap import (
    EXIT_REFUSED,
    EXIT_USAGE,
    Daemon,
    default_socket,
    extension_modules,
    gil_enabled,
    gil_refusal,
    parse_serve,
    run_with_signals,
)
from aconsole.core.infrastructure.fake.server import FakeCore
from aconsole.core.infrastructure.persistence.startup import default_database

if TYPE_CHECKING:
    from pathlib import Path

_SCENARIO_ONLY = 2
_WITH_SOCKET = 4


def socket_from(argv: list[str]) -> tuple[str, str] | None:
    if len(argv) < _SCENARIO_ONLY or argv[0] != "--fake" or not argv[1]:
        return None
    if len(argv) == _SCENARIO_ONLY:
        return argv[1], default_socket()
    if len(argv) == _WITH_SOCKET and argv[2] == "--socket" and argv[3]:
        return argv[1], argv[3]
    return None


_FAKE_USAGE = "usage: python -m aconsole.core --fake SCENARIO [--socket PATH]\n"
_SERVE_USAGE = "       python -m aconsole.core --serve [--socket PATH] [--allow-gil]\n"


def _usage() -> None:
    _ = sys.stdout.write(_FAKE_USAGE + _SERVE_USAGE)


def serve_daemon(socket: Path) -> Daemon:
    """The daemon `__main__` serves. Its tab host uses this same socket path."""
    daemon = Daemon(socket, database=default_database())
    daemon.serve_ingest = True
    return daemon


def _serve(argv: list[str]) -> int:
    parsed = parse_serve(argv)
    if parsed is None:
        _usage()
        return EXIT_USAGE
    socket_path, allow_gil = parsed
    message = gil_refusal(
        allow_gil=allow_gil,
        enabled=gil_enabled(),
        candidates=extension_modules(),
    )
    if message is not None:
        _ = sys.stderr.write(f"{message}\n")
        return EXIT_REFUSED
    return asyncio.run(run_with_signals(serve_daemon(socket_path)))


def main(argv: list[str]) -> int:
    if argv[:1] == ["--serve"]:
        return _serve(argv)
    parsed = socket_from(argv)
    if parsed is None:
        _usage()
        return EXIT_USAGE
    scenario, path = parsed
    core = FakeCore(path, scenario)
    core.start()
    stopped = threading.Event()

    def _on_signal(_signum: int, _frame: object) -> None:
        stopped.set()

    _ = signal.signal(signal.SIGTERM, _on_signal)
    _ = signal.signal(signal.SIGINT, _on_signal)
    try:
        _ = stopped.wait()
    finally:
        core.stop()
    return 0


def console() -> None:
    raise SystemExit(main(sys.argv[1:]))


if __name__ == "__main__":
    console()
