"""Each intentional violation is rejected by its checker."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "gate" / "fixtures"
PY = sys.executable


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PY, str(ROOT / script), *args], capture_output=True, text=True, check=False)


def test_placeholder_body_is_rejected() -> None:
    result = _run("tools/gate/no_placeholders.py", str(FIXTURES / "placeholder_body.py"))
    assert result.returncode != 0
    assert "placeholder-body" in result.stdout


def test_banned_any_is_rejected() -> None:
    result = _run("tools/gate/no_placeholders.py", str(FIXTURES / "banned_any.py"))
    assert result.returncode != 0
    assert "banned-typing" in result.stdout


def test_pyright_file_mode_is_rejected() -> None:
    result = _run("tools/gate/no_placeholders.py", str(FIXTURES / "pyright_mode.py"))
    assert result.returncode != 0
    assert "pyright-file-mode" in result.stdout


def test_type_ignore_is_rejected() -> None:
    result = _run("tools/gate/no_placeholders.py", str(FIXTURES / "type_ignore.py"))
    assert result.returncode != 0
    assert "type-ignore" in result.stdout


def test_noqa_tid251_is_rejected() -> None:
    result = _run("tools/gate/no_placeholders.py", str(FIXTURES / "noqa_tid.py"))
    assert result.returncode != 0
    assert "noqa" in result.stdout


def test_raw_sql_is_rejected() -> None:
    result = _run("tools/check_no_raw_sql.py", str(FIXTURES / "raw_sql.py"))
    assert result.returncode != 0


def test_javascript_is_rejected() -> None:
    result = _run("tools/check_no_js.py", str(FIXTURES / "script.js"))
    assert result.returncode != 0
