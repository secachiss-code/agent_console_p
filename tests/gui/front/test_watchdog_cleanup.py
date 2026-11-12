"""Watchdogs leave no source after success, failure, or actual expiration."""

from __future__ import annotations

import pytest

from tests.gui.watchdog import arm_watchdog


@pytest.mark.parametrize("outcome", ["success", "failure", "timeout"])
def test_application_shutdown_releases_only_its_watchdog(outcome: str) -> None:
    from gi.repository import Gio, GLib

    calls: list[str] = []
    owned: list[int] = []
    failures: list[RuntimeError] = []
    app = Gio.Application(flags=Gio.ApplicationFlags.NON_UNIQUE)

    def expired() -> bool:
        calls.append("expired")
        app.quit()
        return GLib.SOURCE_REMOVE

    def finish() -> bool:
        try:
            if outcome == "failure":
                message = "injected check failure"
                raise RuntimeError(message)  # noqa: TRY301 -- exercise the same failure/quit path as native helpers
        except RuntimeError as error:
            failures.append(error)
        finally:
            app.quit()
        return GLib.SOURCE_REMOVE

    def activate(_app: Gio.Application) -> None:
        app.hold()
        owned.append(arm_watchdog(app, 1 if outcome == "timeout" else 60_000, expired))
        if outcome != "timeout":
            _ = GLib.idle_add(finish)

    _ = app.connect("activate", activate)
    context = GLib.MainContext.default()
    # An unrelated source must survive application shutdown.
    unrelated = GLib.timeout_add(60_000, lambda: GLib.SOURCE_CONTINUE)
    try:
        assert app.run(["watchdog-cleanup"]) == 0
        assert owned
        assert context.find_source_by_id(owned[0]) is None
        assert context.find_source_by_id(unrelated) is not None
        assert calls == (["expired"] if outcome == "timeout" else [])
        assert len(failures) == int(outcome == "failure")
        if failures:
            with pytest.raises(RuntimeError, match="injected check failure"):
                raise failures[0]
    finally:
        _ = GLib.source_remove(unrelated)
