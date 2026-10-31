"""gen_state prints a stable JSON summary of fixture progress files."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "tools" / "gen_state.py"
_FIXTURE = _ROOT / "tests" / "fixtures" / "gen_state"
_EXPECTED = {
    "tasks": [
        {
            "base": "aaa111",
            "checklist": {"open": 0, "ticked": 1},
            "commit": "bbb222",
            "id": "A1",
            "status": "done",
            "status_detail": None,
            "title": "Альфа",
            "unblock": None,
        },
        {
            "base": "ccc333",
            "checklist": {"open": 1, "ticked": 1},
            "commit": None,
            "id": "A2",
            "status": "doing",
            "status_detail": None,
            "title": "Beta",
            "unblock": None,
        },
        {
            "base": "ddd444",
            "checklist": {"open": 1, "ticked": 1},
            "commit": None,
            "id": "A3",
            "status": "blocked",
            "status_detail": "escalation C2",
            "title": "Gamma",
            "unblock": "escalation A3 resolved",
        },
        {
            "base": "recorded by the first hostrun gate",
            "checklist": {"open": 1, "ticked": 0},
            "commit": None,
            "id": "A4",
            "status": "superseded",
            "status_detail": "A4r",
            "title": "Delta",
            "unblock": None,
        },
    ]
}


def test_gen_state_matches_fixture_progress() -> None:
    completed = subprocess.run(
        [sys.executable, str(_SCRIPT), str(_FIXTURE)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0
    assert completed.stdout.endswith("\n")
    assert json.loads(completed.stdout) == _EXPECTED
    again = subprocess.run(
        [sys.executable, str(_SCRIPT), str(_FIXTURE)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert again.stdout == completed.stdout


def test_gen_state_rejects_a_missing_directory(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-progress"
    completed = subprocess.run(
        [sys.executable, str(_SCRIPT), str(missing)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "not a directory" in completed.stderr
