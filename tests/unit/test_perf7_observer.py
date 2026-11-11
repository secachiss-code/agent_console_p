"""The optional frame observer cleans up its own callbacks, including on failure."""

# ruff: noqa: PT009, PT027 -- stdlib runner also works without pytest or GTK.

from __future__ import annotations

import asyncio
import gc
import json
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


class ObserverCleanupTest(unittest.TestCase):
    def test_cleanup_with_and_without_phases_after_success_or_failure(self) -> None:
        for phases in (False, True):
            for fails in (False, True):
                with self.subTest(phases=phases, fails=fails):
                    self._check_cleanup(phases=phases, fails=fails)

    def _check_cleanup(self, *, phases: bool, fails: bool) -> None:
        observer = Path(__file__).resolve().parents[2] / "artifacts/perf7/measure.py"
        hook = runpy.run_path(str(observer))["pytest_runtest_call"]
        with tempfile.TemporaryDirectory() as directory:
            clock = Mock()
            clock.connect.side_effect = [101]
            clock.connect_after.side_effect = [102, 103, 104, 105]
            clock.get_refresh_info.return_value = (0, 16667)
            pane = Mock()
            pane.list_view.add_tick_callback.return_value = 17
            pane.store.get_n_items.return_value = 10000
            window = Mock()
            window.get_frame_clock.return_value = clock
            window.get_renderer.return_value = SimpleNamespace(__gtype__=SimpleNamespace(name="fake"))

            def on_pane(_path: object, _patch: object, _repo: object, check: object) -> None:
                asyncio.run(check(pane, [], window))

            async def check(*_args: object) -> None:
                if fails:
                    message = "injected check failure"
                    raise RuntimeError(message)

            gtk = SimpleNamespace(
                get_major_version=lambda: 4, get_minor_version=lambda: 20, get_micro_version=lambda: 0
            )
            test = SimpleNamespace(_on_pane=on_pane, Gtk=gtk)
            front = ModuleType("tests.gui.front")
            front.test_git_scale_ui = test
            monkeypatch = SimpleNamespace(setattr=setattr)
            item = SimpleNamespace(name="test_scrolling_a_long_history", funcargs={"monkeypatch": monkeypatch})
            log = Path(directory) / "observation.json"
            environment = {"ACONSOLE_PERF7_LOG": str(log)}
            if phases:
                environment["ACONSOLE_PERF7_PHASES"] = "1"
            callbacks_before = gc.callbacks.copy()
            with patch.dict(sys.modules, {"tests.gui.front": front}), patch.dict(os.environ, environment, clear=True):
                hook(item)
                if fails:
                    with self.assertRaisesRegex(RuntimeError, "injected check failure"):
                        test._on_pane(None, monkeypatch, None, check)
                else:
                    test._on_pane(None, monkeypatch, None, check)
            pane.list_view.remove_tick_callback.assert_called_once_with(17)
            self.assertEqual(
                [call.args[0] for call in clock.disconnect.call_args_list], [101, 102, 103, 104, 105] if phases else []
            )
            self.assertEqual(gc.callbacks, callbacks_before)
            self.assertEqual(json.loads(log.read_text())["rows"], 10000)
