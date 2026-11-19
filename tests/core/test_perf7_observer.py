"""The AConsole 0.1.4 core RSS budget and its measurement report."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tools import ui_rss

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("kib", "exit_code", "verdict"),
    [(188_221, 0, "PASS"), (188_222, 1, "FAIL")],
)
def test_aconsole_0_1_4_core_rss_budget_reports_and_fails_above_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kib: int, exit_code: int, verdict: str
) -> None:
    def measured(pid: int) -> int:
        assert pid == 123
        return kib

    report = tmp_path / "rss.md"
    monkeypatch.setattr(ui_rss, "rss_kib", measured)

    assert ui_rss.main(["--pid", "123", "--core-budget-report", str(report)]) == exit_code
    text = report.read_text(encoding="utf-8")
    assert f"**{verdict}**" in text
    assert f"({kib} КиБ)" in text
    assert "175.81 МиБ" in text
    assert "183.81 МиБ" in text
    assert "--pid 123 --core-budget-report" in text


def test_explicit_rss_limit_remains_available(monkeypatch: pytest.MonkeyPatch) -> None:
    def measured(_pid: int) -> int:
        return 151 * 1024

    monkeypatch.setattr(ui_rss, "rss_kib", measured)

    assert ui_rss.main(["--pid", "123", "--limit-mib", "150"]) == 1
    assert ui_rss.main(["--pid", "123", "--limit-mib", "151"]) == 0
