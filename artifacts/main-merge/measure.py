# Standalone filesystem-loaded pytest plugin, not a Python package.
# ruff: noqa: INP001
"""Observe the unchanged scale test; optional profiling is a separate diagnostic run."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import pytest
    from gi.repository import Adw, Gdk, Gtk

    from aconsole.contracts.git import GitNodeView, GitStateResult
    from aconsole.ui.views.review.git_pane import GitPane, _RowWidget


import cProfile
import gc
import json
import os
import time
from pathlib import Path


def read(path: str | Path) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def pytest_runtest_call(item: pytest.Function) -> None:  # noqa: C901 -- keep timing callbacks and their cleanup together in this standalone probe
    if item.name != "test_scrolling_a_long_history":
        return
    from tests.gui.front import test_git_scale_ui as test  # noqa: PLC0415 -- load the optional diagnostic only when its target test runs

    original = test._on_pane  # noqa: SLF001 -- observe the original private fixture without editing its workload
    label_stats = {}
    if os.environ.get("ACONSOLE_PERF7_LABELS"):
        from aconsole.ui.views.review.git_pane import _RowWidget  # noqa: PLC0415 -- load the optional diagnostic only when its target test runs

        show_node = _RowWidget._show_node  # noqa: SLF001 -- observe the original private fixture without editing its workload

        class Proxy:
            def __init__(self, widget: Gtk.Widget, name: str) -> None:
                self.widget = widget
                self.name = name

            def __getattr__(self, key: str) -> Callable[..., object]:
                method = getattr(self.widget, key)

                def timed(*args: object, **kwargs: object) -> object:
                    start = time.perf_counter_ns()
                    try:
                        return method(*args, **kwargs)
                    finally:
                        stat = label_stats.setdefault(self.name + "." + key, [0, 0])
                        stat[0] += 1
                        stat[1] += time.perf_counter_ns() - start

                return timed

        def show(self: _RowWidget, node: GitNodeView, state: GitStateResult | None) -> None:
            if not isinstance(self.title, Proxy):
                self.title = Proxy(self.title, "title")
                self.when = Proxy(self.when, "when")
            return show_node(self, node, state)

        item.funcargs["monkeypatch"].setattr(_RowWidget, "_show_node", show)

    def on_pane(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        repo: Path,
        check: Callable[[GitPane, list[str], Adw.ApplicationWindow], Awaitable[None]],
    ) -> None:
        async def observed(pane: GitPane, toasts: list[str], window: Adw.ApplicationWindow) -> None:
            data = {
                "renderer": window.get_renderer().__gtype__.name,
                "python": os.sys.version,
                "gtk_env": {k: v for k, v in os.environ.items() if k.startswith(("GSK_", "GDK_", "LIBGL_", "LP_"))},
                "cpu_count": os.cpu_count(),
                "affinity": sorted(os.sched_getaffinity(0)),
                "cpu_max": read("/sys/fs/cgroup/cpu.max"),
                "cpu_stat_before": read("/sys/fs/cgroup/cpu.stat"),
                "load_before": os.getloadavg(),
                "proc_before": read("/proc/self/status"),
                "frames": [],
                "gc": [],
            }
            started = time.perf_counter()
            cpu = time.process_time()
            thread = time.thread_time()
            last = [started, cpu, thread]

            def tick(_widget: Gtk.Widget, _clock: Gdk.FrameClock) -> bool:
                now = [time.perf_counter(), time.process_time(), time.thread_time()]
                data["frames"].append(
                    {
                        "at": now[0] - started,
                        "wall": now[0] - last[0],
                        "cpu": now[1] - last[1],
                        "thread": now[2] - last[2],
                        "rows": pane.store.get_n_items(),
                        "scroll": pane.scroller.get_vadjustment().get_value(),
                    }
                )
                last[:] = now
                return True

            def collection(phase: str, info: dict[str, int]) -> None:
                data["gc"].append(dict(phase=phase, at=time.perf_counter() - started, **info))

            handle = pane.list_view.add_tick_callback(tick)
            gc.callbacks.append(collection)
            profile = cProfile.Profile() if os.environ.get("ACONSOLE_PERF7_PROFILE") else None
            try:
                if profile:
                    profile.enable()
                await check(pane, toasts, window)
            finally:
                if profile:
                    profile.disable()
                    profile.dump_stats(os.environ["ACONSOLE_PERF7_PROFILE"])
                gc.callbacks.remove(collection)
                pane.list_view.remove_tick_callback(handle)
                data.update(
                    label_stats=label_stats,
                    cpu_stat_after=read("/sys/fs/cgroup/cpu.stat"),
                    load_after=os.getloadavg(),
                    proc_after=read("/proc/self/status"),
                    rows=pane.store.get_n_items(),
                    wall=time.perf_counter() - started,
                    cpu=time.process_time() - cpu,
                    thread=time.thread_time() - thread,
                )
                Path(os.environ["ACONSOLE_PERF7_LOG"]).write_text(json.dumps(data) + "\n")  # noqa: ASYNC240 -- diagnostic filesystem work is outside the measured workload

        return original(tmp_path, monkeypatch, repo, observed)

    monkeypatch = item.funcargs["monkeypatch"]
    monkeypatch.setattr(test, "_on_pane", on_pane)
