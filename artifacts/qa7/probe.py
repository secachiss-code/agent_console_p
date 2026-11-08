# Standalone filesystem-loaded pytest plugin, not a Python package.
# ruff: noqa: INP001
"""Instrument the existing test in memory; retain paste assertions and delays."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


import ast
import json
import os
import textwrap
import time
from pathlib import Path


def record(event: str, mode: str, title: str | None, data: object = None) -> None:
    path = Path(os.environ["ACONSOLE_QA7_LOG"])
    with path.open("a") as stream:
        stream.write(
            json.dumps({"at": time.monotonic(), "event": event, "mode": mode, "title": title, "data": data}) + "\n"
        )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Function]) -> None:
    if os.environ.get("ACONSOLE_QA7_MODE") == "prefix":
        kept = [
            item
            for item in items
            if str(item.path).split("/")[-1] <= "test_clipboard_routing.py" and "/tests/gui/front/" in str(item.path)
        ]
        dropped = [item for item in items if item not in kept]
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept
    if os.environ.get("ACONSOLE_QA7_PROBE") != "1":
        return
    modules = {item.module for item in items if item.path.name == "test_clipboard_routing.py"}
    for module in modules:
        baseline = (Path(__file__).parent / "baseline-clipboard.txt").read_text()
        tree = ast.parse(baseline)
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "_ClipboardApp")
        method = next(node for node in cls.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_open")
        source = textwrap.dedent("\n".join(baseline.splitlines()[method.lineno - 1 : method.end_lineno]))
        source = source.replace("await super()._open()", "await AConsoleApp._open(self)")
        source = source.replace(
            "self.received.extend(text.encode())",
            (
                '(qa7_record("commit",self.mode,_vte.get_window_title(),t'
                "ext.encode().hex()),self.received.extend(text.encode()))"
            ),
        )
        source = source.replace(
            "page.focus_terminal()",
            (
                'page.terminal.connect("window-title-changed",lambda vte: qa7_record("p'
                'arsed",self.mode,vte.get_window_title()))\n        page.focus_terminal()'
            ),
        )
        # OSC title follows the enable sequence in the same child stream. Its notification
        # proves that VTE parsed all preceding bytes, rather than just that the child wrote them.
        source = source.replace(
            'f"open(',
            '"os.write(1,bytes.fromhex(\'1b5d303b7161372d7061727365722d726561647907\'));"\n                f"open(',
        )
        source = source.replace(
            "keyboard.press(Gdk.KEY_v, control=True, shift=True)",
            (
                'qa7_record("before_paste",self.mode,page.terminal.get_window_title(),rea'
                "dy.exists())\n        keyboard.press(Gdk.KEY_v, control=True, shift=True)"
            ),
        )
        namespace = dict(module.__dict__, qa7_record=record)
        exec(compile(source, module.__file__, "exec"), namespace)  # noqa: S102 -- compile the tracked baseline test with instrumentation; retain its assertions
        module._ClipboardApp._open = namespace["_open"]  # noqa: SLF001 -- observe the original private fixture without editing its workload
