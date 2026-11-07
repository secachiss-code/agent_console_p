"""A start that fails stays on screen, says why and where the log is, and can be tried again."""

from __future__ import annotations

import asyncio
import tempfile
from typing import TYPE_CHECKING, override

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from aconsole.contracts.errors import ErrorObject  # noqa: E402
from aconsole.core.infrastructure.fake.server import FakeCore  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreCallError, CoreClient, core_log_path  # noqa: E402
from aconsole.ui.client.error_text import error_text  # noqa: E402
from aconsole.ui.views.app import AConsoleApp  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable

    from aconsole.contracts.ipc import InitializeResult


class _FailsFirst(CoreClient):
    def __init__(self, path: str, failures: int) -> None:
        super().__init__(path)
        self.failures: int = failures

    @override
    async def connect(self) -> InitializeResult:
        if self.failures > 0:
            self.failures -= 1
            message = "the socket is not there"
            raise OSError(message)
        return await super().connect()


class _App(AConsoleApp):
    def __init__(self, client: CoreClient) -> None:
        super().__init__(client)
        self.seen: dict[str, str] = {}
        self.window_after_retry: bool = False
        self.problem: BaseException | None = None

    @override
    def do_activate(self) -> None:
        super().do_activate()
        _ = spawn_task(self._watch())

    async def _watch(self) -> None:
        try:
            await self._drive()
        except BaseException as error:  # noqa: BLE001 -- reported after the loop stops
            self.problem = error
            self.quit()

    async def _drive(self) -> None:
        await _until(lambda: self.failed and self.startup_page is not None)
        assert self.startup_window is not None
        assert self.startup_page is not None
        assert self.startup_retry is not None
        assert self.startup_diagnostics is not None
        assert self.startup_window.get_visible()
        self.seen["reason"] = self.startup_page.get_description() or ""
        assert self.startup_diagnostics.get_visible()
        self.seen["log"] = self._startup_error
        assert self.failed
        self.startup_retry.emit("clicked")
        await _until(lambda: self.window is not None)
        self.window_after_retry = True
        assert self.startup_window is None
        assert not self.failed
        assert self.window is not None
        self.window.destroy()
        await self.client.close()
        self.quit()


async def _until(check: Callable[[], bool]) -> None:
    for _ in range(400):
        if check():
            return
        await asyncio.sleep(0.02)
    message = "condition not reached"
    raise AssertionError(message)


def test_a_failed_start_says_why_and_can_be_retried() -> None:
    path = f"{tempfile.mkdtemp(prefix='aconsole-gui-')}/core.sock"
    fake = FakeCore(path, "waiting")
    fake.start()
    try:
        app = _App(_FailsFirst(path, 1))
        assert run_app(lambda: app, ["startup-test"]) == 0
    finally:
        fake.stop()
    if app.problem is not None:
        raise app.problem
    assert "the socket is not there" in app.seen["reason"]
    assert str(core_log_path()) in app.seen["log"]
    assert str(core_log_path()) in app.seen["reason"]
    assert app.window_after_retry


def test_an_error_reads_as_a_sentence_and_an_unknown_one_points_to_the_log() -> None:
    known = CoreCallError(ErrorObject(code=2003, message="TAB_NOT_FOUND"))
    assert "TAB_NOT_FOUND" not in str(known)
    assert "2003" not in str(known)
    unknown = error_text(ErrorObject(code=1, message="SOMETHING_NEW"))
    assert "SOMETHING_NEW" in unknown
    assert str(core_log_path()) in unknown
