"""Trace original test scenarios without replacing assertions or GUI behavior."""

import functools
import json
import sys
import time
from pathlib import Path

import pytest

from aconsole.ui.views.setup.preferences import PreferencesDialog
from aconsole.ui.views.window import MainWindow

output = Path(__file__).resolve().parent
label = sys.argv[1]
trace_path = output / (label + ".jsonl")
if trace_path.exists() or (output / (label + ".xml")).exists():
    raise FileExistsError(label)
started = time.monotonic()


def wrap(cls, name):
    original = getattr(cls, name)

    @functools.wraps(original)
    def traced(self, *args, **kwargs):
        def emit(phase):
            event = dict(seconds=time.monotonic() - started, method=name, phase=phase,
                         args=[str(arg) for arg in args], mapped=self.get_mapped(),
                         visible=self.get_visible(), width=self.get_width(), height=self.get_height())
            if isinstance(self, MainWindow):
                event.update(active=self.is_active(), selected=int(self.selection.get_selected()))
            else:
                event.update(active=self._active, prompt=str(self._close_prompt), can_close=self.get_can_close())
            with trace_path.open("a") as stream:
                stream.write(json.dumps(event) + "\n")
        emit("before")
        result = original(self, *args, **kwargs)
        emit("after")
        return result

    setattr(cls, name, traced)


for method in ("_sync_approvals", "_activity_changed", "_post_alert", "_withdraw_alert"):
    wrap(MainWindow, method)
for method in ("_close_attempt", "_close_response", "_discard_after_prompt", "_closed"):
    wrap(PreferencesDialog, method)

tests = ([
    "tests/gui/front/test_b0_window.py", "tests/gui/front/test_b1_keys.py",
    "tests/gui/front/test_des3_visual.py", "tests/gui/front/test_approval_alerts.py",
    "tests/gui/front/test_preferences_drafts.py",
] if label == "desktop-full-trace" else [
    "tests/gui/front/test_approval_alerts.py::test_the_window_rings_withdraws_and_a_click_opens_the_tab",
    "tests/gui/front/test_preferences_drafts.py::test_closing_dirty_preferences_can_cancel_or_discard",
])
raise SystemExit(pytest.main([
    "-o", "addopts=", "-q", "--junitxml=" + str(output / (label + ".xml")),
    *tests,
]))
