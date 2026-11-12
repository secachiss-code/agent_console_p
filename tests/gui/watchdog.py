"""A test application's watchdog belongs to that application's lifetime."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from gi.repository import Gio


def arm_watchdog(app: Gio.Application, interval_ms: int, expired: Callable[[], bool]) -> int:
    from gi.repository import GLib

    source_id: int | None = None
    shutdown_id: int | None = None

    def shutdown(_app: Gio.Application) -> None:
        nonlocal source_id, shutdown_id
        if source_id is not None:
            _ = GLib.source_remove(source_id)
            source_id = None
        if shutdown_id is not None:
            app.disconnect(shutdown_id)
            shutdown_id = None

    def fire() -> bool:
        nonlocal source_id
        # Clear before quit() can emit shutdown: an expired source must not be
        # removed again. All test watchdogs are one-shot.
        source_id = None
        _ = expired()
        return GLib.SOURCE_REMOVE

    source_id = GLib.timeout_add(interval_ms, fire)
    shutdown_id = app.connect("shutdown", shutdown)
    return source_id
