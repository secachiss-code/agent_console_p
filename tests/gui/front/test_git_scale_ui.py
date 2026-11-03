"""The Git page's list under a long history, on the real core. Xvfb draws in software, so frame times say little about
a GPU. What the page itself costs in memory and main-loop stalls does carry over.

Memory is held as growth: an empty libadwaita window takes 177 MB of RSS here, so an absolute 150 MB cannot hold.
Before this stops being a prototype, measure on a real GPU and choose an absolute limit from that.
"""

from __future__ import annotations

import os
import statistics
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Final

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk  # noqa: E402

from tests.gui.front.test_git_pane import _GIT, _git, _on_pane, _project, _settled  # noqa: E402

if TYPE_CHECKING:
    import pytest
    from gi.repository import Adw, Gdk

    from aconsole.ui.views.review.git_pane import GitPane

_COMMITS: Final = 10_000
_NARROW: Final = 700
_SEAM: Final = 4
_SCROLL_S: Final = 4.0
_GROWTH_LIMIT_MB: Final = 150.0
_PAGE_ATTEMPTS: Final = 60
_KIB_PER_MIB: Final = 1024.0
_MS: Final = 1000.0
_MIN_FRAMES: Final = 30
_FRAME_LIMIT_MS: Final = 17.5
_P95_INDEX: Final = 18
_PERCENTILES: Final = 20


def _rss_mb() -> float:
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / _KIB_PER_MIB
    message = "no VmRSS"
    raise AssertionError(message)


def _report(line: str) -> None:
    report = os.environ.get("ACONSOLE_SCALE_REPORT")
    if report:
        _ = Path(report).write_text(line + "\n", encoding="utf-8")


def test_a_narrow_page_puts_the_inspector_under_the_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _tree = _project(tmp_path)

    async def check(pane: GitPane, _toasts: list[str], window: Adw.ApplicationWindow) -> None:
        await _settled(lambda: pane.store.get_n_items() >= 4)
        assert pane.body.get_orientation() == Gtk.Orientation.HORIZONTAL
        window.set_default_size(_NARROW, _NARROW)
        await _settled(lambda: pane.body.get_orientation() == Gtk.Orientation.VERTICAL)
        inspector = pane.body.get_end_child()
        assert inspector is not None
        await _settled(lambda: inspector.get_width() > 0)
        assert inspector.get_width() >= pane.get_width() - _SEAM
        pane.selection.set_selected(0)
        button = pane.git_actions.commit_button
        await _settled(lambda: button.get_mapped() and button.get_height() > 0)
        placed, bounds = button.compute_bounds(window)
        assert placed
        assert bounds.get_y() + bounds.get_height() <= window.get_height()

    _on_pane(tmp_path, monkeypatch, repo, check)


def test_scrolling_a_long_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _ = _git(repo, "init", "-q", "-b", "develop")
    lines: list[str] = []
    for number in range(_COMMITS):
        message = f"commit {number}"
        lines.extend((
            "commit refs/heads/develop",
            f"committer t <t@e> {1_700_000_000 + number} +0000",
            f"data {len(message)}",
            message,
            f"M 100644 inline f{number % 200}.txt",
            f"data {len(message)}",
            message,
            "",
        ))
    _ = subprocess.run(
        [_GIT, "-C", str(repo), "fast-import", "--quiet"], input="\n".join(lines), text=True, check=True
    )
    _ = _git(repo, "checkout", "-qf", "develop")

    async def check(pane: GitPane, _toasts: list[str], _window: Adw.ApplicationWindow) -> None:
        before = _rss_mb()
        marks = [f"start {before:.0f}"]
        await _settled(lambda: pane.store.get_n_items() >= 200)
        marks.append(f"first page {_rss_mb():.0f}")
        loading = time.perf_counter()
        for _ in range(_PAGE_ATTEMPTS):
            if pane.store.get_n_items() >= _COMMITS:
                break
            await pane.load_more()
        await _settled(lambda: pane.store.get_n_items() >= _COMMITS)
        loaded = time.perf_counter() - loading
        marks.append(f"all rows {_rss_mb():.0f} after {loaded:.1f} s")
        adjustment = pane.scroller.get_vadjustment()
        frames: list[float] = []
        started = time.perf_counter()
        last = started

        def tick(_widget: Gtk.Widget, _clock: Gdk.FrameClock) -> bool:
            nonlocal last
            now = time.perf_counter()
            frames.append(now - last)
            last = now
            span = adjustment.get_upper() - adjustment.get_page_size()
            progress = (now - started) / _SCROLL_S
            adjustment.set_value(span * min(progress, 1.0))
            return now - started < _SCROLL_S

        _ = pane.list_view.add_tick_callback(tick)
        await _settled(lambda: time.perf_counter() - started >= _SCROLL_S)
        assert len(frames) >= _MIN_FRAMES, f"only {len(frames)} frames"
        median = statistics.median(frames) * _MS
        p95 = statistics.quantiles(frames, n=_PERCENTILES)[_P95_INDEX] * _MS
        rss = _rss_mb()
        line = (
            f"git scale: {pane.store.get_n_items()} rows, {len(frames)} frames in {_SCROLL_S:.0f} s, "
            f"median {median:.1f} ms, p95 {p95:.1f} ms, worst {max(frames) * _MS:.1f} ms, "
            f"RSS {rss:.0f} MB ({', '.join(marks)})"
        )
        _report(line)
        assert adjustment.get_value() > 0, "the list never moved"
        assert median <= _FRAME_LIMIT_MS, line
        assert rss - before <= _GROWTH_LIMIT_MB, line

    _on_pane(tmp_path, monkeypatch, repo, check)
