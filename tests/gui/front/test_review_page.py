"""The review page against the fake core: files, hunks, comments, and restore."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from aconsole.contracts.common import TabId  # noqa: E402
from aconsole.ui.bootstrap import run_app, spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.viewmodels.review_vm import ReviewViewModel, files_preview, gutter  # noqa: E402
from aconsole.ui.views.review.page import ReviewPage  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_TIMEOUT_MS = 8000


def _on_page(socket: str, check: Callable[[ReviewPage, list[str]], Awaitable[None]]) -> None:
    failures: list[BaseException] = []

    class _App(Adw.Application):
        def __init__(self) -> None:
            super().__init__(application_id="io.github.aconsole.ReviewTest")
            self._held: bool = False

        def do_activate(self) -> None:
            if self._held:
                return
            self._held = True
            self.hold()
            _ = GLib.timeout_add(_TIMEOUT_MS, self._expired)
            _ = spawn_task(self._run())

        def _expired(self) -> bool:
            failures.append(TimeoutError("the page did not settle"))
            self.quit()
            return GLib.SOURCE_REMOVE

        async def _run(self) -> None:
            client = CoreClient(socket)
            try:
                _ = await client.connect()
                pulled, _subscribed = await client.pull_snapshot()
                snapshot = pulled.snapshot
                toasts: list[str] = []
                viewmodel = ReviewViewModel(client, TabId(snapshot.tabs[0].id))
                page = ReviewPage(viewmodel, "tab", lambda: None, toasts.append)
                window = Adw.ApplicationWindow(application=self, content=page)
                window.present()
                await check(page, toasts)
            except BaseException as error:  # noqa: BLE001 -- reported after the loop stops
                failures.append(error)
            finally:
                await client.close()
                self.release()
                self.quit()

    assert run_app(_App, ["review-test"]) == 0
    if failures:
        raise failures[0]


def _labels(widget: Gtk.Widget) -> list[str]:
    found: list[str] = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            found.append(child.get_label())
        found.extend(_labels(child))
        child = child.get_next_sibling()
    return found


def _text_views(widget: Gtk.Widget) -> list[Gtk.TextView]:
    found: list[Gtk.TextView] = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.TextView):
            found.append(child)
        found.extend(_text_views(child))
        child = child.get_next_sibling()
    return found


def _text(view: Gtk.TextView) -> str:
    buffer = view.get_buffer()
    return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), include_hidden_chars=True)


async def _settled(check: Callable[[], bool]) -> None:
    for _ in range(200):
        if check():
            return
        await asyncio.sleep(0.02)
    message = "condition not reached"
    raise AssertionError(message)


def test_files_and_hunk_of_the_selected_file(running_app: str) -> None:
    async def check(page: ReviewPage, _toasts: list[str]) -> None:
        await _settled(lambda: bool(_text_views(page.diff_box)))
        assert page.selected_path == "a.py"
        assert "a.py" in _labels(page.file_list)
        text = _text(_text_views(page.diff_box)[0])
        assert "fsum(items)" in text
        assert "    2 -" in text or "2       -" in text

    _on_page(running_app, check)


def test_comment_reports_back(running_app: str) -> None:
    async def check(page: ReviewPage, toasts: list[str]) -> None:
        await page.send_comment("a.py", "h1", "поправь")
        assert len(toasts) == 1

    _on_page(running_app, check)


@pytest.mark.parametrize("rejected", [False, True])
def test_failed_comment_restores_the_draft_when_reopened(
    running_app: str, monkeypatch: pytest.MonkeyPatch, *, rejected: bool,
) -> None:
    from aconsole.contracts.app import CommandResult

    async def check(page: ReviewPage, toasts: list[str]) -> None:
        async def failed_comment(_path: str, _hunk: str, _text: str) -> CommandResult:
            if rejected:
                return CommandResult(ok=False, toast=None)
            message = "core unavailable"
            raise ConnectionError(message)

        monkeypatch.setattr(page.viewmodel, "comment", failed_comment)
        await page.send_comment("a.py", "h1", "Keep this comment")
        assert toasts
        page._ask_comment(Gtk.Button(), "a.py", "h1")
        window = page.get_root()
        assert isinstance(window, Adw.ApplicationWindow)
        dialog = window.get_visible_dialog()
        assert isinstance(dialog, Adw.AlertDialog)
        entry = dialog.get_extra_child()
        assert isinstance(entry, Gtk.Entry)
        assert entry.get_text() == "Keep this comment"
        dialog.force_close()

    _on_page(running_app, check)


def test_checkpoints_are_listed_newest_first_and_restore(running_app: str) -> None:
    async def check(page: ReviewPage, toasts: list[str]) -> None:
        await _settled(lambda: page.checkpoint_list.get_row_at_index(0) is not None)
        points = await page.viewmodel.checkpoints()
        assert points[0].turn > points[-1].turn
        row = page.checkpoint_list.get_row_at_index(0)
        assert isinstance(row, Adw.ActionRow)
        assert row.get_title() == points[0].label
        await page.restore(points[0], "yes")
        assert toasts

    _on_page(running_app, check)


def test_a_prompt_with_markup_is_shown_as_text(running_app: str) -> None:
    async def check(page: ReviewPage, _toasts: list[str]) -> None:
        from aconsole.ui.views.review.page import _markup_safe

        assert _markup_safe("a <b> & c") == "a &lt;b&gt; &amp; c"
        assert page.switcher is not None

    _on_page(running_app, check)


def test_gutter_and_file_preview() -> None:
    assert gutter(3, None, "del") == "    3       - "
    assert gutter(None, 12, "add") == "         12 + "
    assert files_preview(("a", "b", "c"), limit=2) == (("a", "b"), 1)


@pytest.mark.parametrize("failed", [False, True])
def test_late_file_cannot_replace_a_new_summary(
    running_app: str, monkeypatch: pytest.MonkeyPatch, *, failed: bool,
) -> None:
    from aconsole.contracts.app import DiffFileResult, DiffSummaryResult

    async def check(page: ReviewPage, _toasts: list[str]) -> None:
        await _settled(lambda: bool(_text_views(page.diff_box)))
        original_file = page.viewmodel.file
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_file(path: str) -> DiffFileResult:
            started.set()
            await release.wait()
            return await original_file(path)

        async def empty_summary() -> DiffSummaryResult:
            if failed:
                message = "summary unavailable"
                raise ConnectionError(message)
            return DiffSummaryResult(base="base", head="head", files=(), insertions=0, deletions=0)

        monkeypatch.setattr(page.viewmodel, "file", delayed_file)
        monkeypatch.setattr(page.viewmodel, "summary", empty_summary)
        task = asyncio.create_task(page._load_file("a.py"))
        await started.wait()
        await page.load_changes()
        release.set()
        await task
        assert page.selected_path is None
        assert not _text_views(page.diff_box)
        assert _labels(page.diff_box)

    _on_page(running_app, check)
