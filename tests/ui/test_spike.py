"""Exercise the verified GTK spike so the UI modules stay in the coverage gate.

Imports stay inside the tests. Importing ``gi`` enables the GIL for the process,
and the core smoke test must still observe a free-threaded interpreter.
"""

from pathlib import Path

import pytest


def test_no_arguments_start_the_real_core(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001 -- pytest fixture
    from aconsole.__main__ import run_ui
    from aconsole.ui.client.daemon import core_python, core_socket

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("ACONSOLE_CORE_PYTHON", "/opt/core/python")
    assert core_socket() == str(tmp_path / "aconsole" / "core.sock")
    assert core_python() == Path("/opt/core/python")
    monkeypatch.delenv("ACONSOLE_CORE_PYTHON")
    venv = tmp_path / ".venv-core" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").touch()
    assert core_python(tmp_path / ".venv-ui") == venv / "python"
    # A mistyped option must still be refused rather than start anything.
    with pytest.raises(SystemExit):
        _ = run_ui(["--nope"])


def test_missing_core_interpreter_is_named(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001 -- pytest fixture
    from aconsole.ui.client.daemon import core_python

    monkeypatch.delenv("ACONSOLE_CORE_PYTHON", raising=False)
    with pytest.raises(FileNotFoundError, match="ACONSOLE_CORE_PYTHON"):
        _ = core_python(tmp_path / "ui")


def test_spike_paints_and_quits(tmp_path: Path) -> None:
    from aconsole.ui.spike import run_spike

    png = Path(tmp_path) / "spike.png"
    p99 = run_spike(snapshot=png, autoquit=True)
    assert p99 >= 0
    assert png.stat().st_size > 0
