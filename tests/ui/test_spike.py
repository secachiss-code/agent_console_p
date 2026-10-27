"""Exercise the verified GTK spike so the UI modules stay in the coverage gate.

Imports stay inside the tests. Importing ``gi`` enables the GIL for the process,
and the core smoke test must still observe a free-threaded interpreter.
"""

from pathlib import Path


def test_usage_without_spike() -> None:
    from aconsole.__main__ import main

    assert main([]) == 2


def test_spike_paints_and_quits(tmp_path: Path) -> None:
    from aconsole.ui.spike import run_spike

    png = Path(tmp_path) / "spike.png"
    p99 = run_spike(snapshot=png, autoquit=True)
    assert p99 >= 0
    assert png.stat().st_size > 0
