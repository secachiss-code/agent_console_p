"""The contrast gate: it judges the real style sheet, and fails on a color that does not pass."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import contrast_check

_STYLE = Path(__file__).resolve().parents[1] / "src" / "aconsole" / "ui" / "style" / "style.css"


def _judged(rule: str) -> list[contrast_check.Failure]:
    """What the gate says about one more rule added to the real sheet, which itself passes."""
    return contrast_check.check_css(_STYLE.read_text(encoding="utf-8") + rule)


def test_black_on_white_passes_wcag() -> None:
    assert contrast_check.contrast_ratio("#000000", "#ffffff") >= contrast_check.WCAG_AA


def test_the_style_sheet_and_the_terminal_pass_in_both_themes() -> None:
    assert [str(item) for item in contrast_check.check_css(_STYLE.read_text(encoding="utf-8"))] == []
    assert [str(item) for item in contrast_check.check_terminal()] == []


def test_the_gate_reads_real_rules_from_the_sheet() -> None:
    _defined, rules = contrast_check.parse_css(_STYLE.read_text(encoding="utf-8"))
    selectors = {rule.selector for rule in rules if "color" in rule.declarations}
    assert ".ac-tab-subtitle" in selectors
    assert ".ac-approval-risk" in selectors
    assert ".dim-label" in selectors


def test_pale_text_without_a_background_fails_in_the_theme_where_it_is_pale() -> None:
    assert [(item.theme, item.what) for item in _judged(".x { color: #bbbbbb; }")] == [("light", ".x (#bbbbbb)")]


def test_a_rule_without_a_background_must_pass_on_every_surface() -> None:
    # Passes on white and fails only on the sidebar.
    assert [item.theme for item in _judged(".x { color: #767676; }")] == ["light", "dark"]
    assert contrast_check.contrast_ratio("#767676", "#ffffff") >= contrast_check.WCAG_AA


def test_a_background_of_its_own_is_what_counts() -> None:
    css = ".x { color: #000000; background-color: #ffffff; } .y { color: #ffffff; background-color: #ffff00; }"
    assert [item.what for item in _judged(css)] == [".y (#ffffff)", ".y (#ffffff)"]


def test_opacity_on_the_rule_counts_against_it() -> None:
    assert _judged(".x { color: #000000; background-color: #ffffff; }") == []
    assert len(_judged(".x { color: #000000; background-color: #ffffff; opacity: 0.3; }")) == 2


def test_a_shape_that_carries_meaning_needs_three_to_one() -> None:
    sheet = _STYLE.read_text(encoding="utf-8").replace(
        "@define-color ac_warning_shape @warning_color;", "@define-color ac_warning_shape @warning_bg_color;",
    )
    failures = contrast_check.check_css(sheet)
    assert [(item.what, item.needed) for item in failures] == [("bar: warning", contrast_check.WCAG_GRAPHICS)]


def test_colors_are_resolved_as_gtk_does() -> None:
    colors = {"fg": (1.0, 1.0, 1.0, 0.5)}
    assert contrast_check.resolve("alpha(@fg, 0.5)", colors)[3] == pytest.approx(0.25)
    assert contrast_check.resolve("mix(black, white, 0.25)", colors)[:3] == pytest.approx((0.25, 0.25, 0.25))
    assert contrast_check.over((0.0, 0.0, 0.0, 0.5), (1.0, 1.0, 1.0, 1.0))[0] == pytest.approx(0.5)


def test_a_color_the_gate_cannot_read_is_an_error_not_a_pass() -> None:
    with pytest.raises(ValueError, match="unknown color"):
        _ = _judged(".x { color: @no_such_color; }")
    with pytest.raises(ValueError, match="cannot read"):
        _ = _judged(".x { color: rgb(1, 2, 3); }")


def test_the_command_line_fails_when_a_pair_fails() -> None:
    assert contrast_check.main(["#000000", "#ffffff"]) == 0
    assert contrast_check.main(["#777777", "#888888"]) == 1
    assert contrast_check.main(["#000000"]) == 2


def test_no_text_is_faded_where_the_gate_cannot_see_it() -> None:
    views = _STYLE.parents[1] / "views"
    assert [str(path) for path in views.rglob("*.py") if "fgalpha" in path.read_text(encoding="utf-8")] == []


@pytest.mark.parametrize("regression", [False, True])
def test_default_invocation_checks_css_instead_of_silently_passing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *, regression: bool,
) -> None:
    sheet = tmp_path / "style.css"
    css = _STYLE.read_text(encoding="utf-8")
    if regression:
        css += "\n.contrast-regression { color: #bbbbbb; }\n"
    sheet.write_text(css, encoding="utf-8")
    monkeypatch.setattr(contrast_check, "_STYLE", sheet)
    assert contrast_check.main([]) == (1 if regression else 0)
    output = capsys.readouterr().out
    assert "contrast_check:" in output
    if regression:
        assert "light: .contrast-regression" in output
    else:
        assert "0 below the minimum" in output
