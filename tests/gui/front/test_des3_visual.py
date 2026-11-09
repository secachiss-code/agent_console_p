"""Pixel baselines and risk semantics: geometry or color regressions must fail."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import gi
import msgspec
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gtk  # noqa: E402

from aconsole.contracts.app import EditDetail  # noqa: E402
from aconsole.contracts.common import RiskTag  # noqa: E402
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.style import stylesheet  # noqa: E402
from aconsole.ui.viewmodels.approvals_vm import ApprovalsViewModel  # noqa: E402
from aconsole.ui.views.approvals.panel import ApprovalCard, ApprovalPanel  # noqa: E402

_BASE = Path(__file__).resolve().parents[1] / "snapshots" / "DES3"
_ROOT = Path(__file__).resolve().parents[3]


def pixel_difference(first: Path, second: Path) -> float:
    left = GdkPixbuf.Pixbuf.new_from_file(str(first))
    right = GdkPixbuf.Pixbuf.new_from_file(str(second))
    assert left is not None
    assert right is not None
    assert (left.get_width(), left.get_height(), left.get_n_channels(), left.get_rowstride()) == (
        right.get_width(), right.get_height(), right.get_n_channels(), right.get_rowstride(),
    )
    a, b = left.get_pixels(), right.get_pixels()
    assert a is not None
    assert b is not None
    return sum(abs(x - y) > 8 for x, y in zip(a, b, strict=True)) / len(a)


@pytest.mark.parametrize(("theme", "width", "name"), [
    ("light", 1280, "light"), ("dark", 1280, "dark"), ("light", 700, "narrow"),
])
def test_native_widgets_match_reviewed_baselines(tmp_path: Path, theme: str, width: int, name: str) -> None:
    output = tmp_path / f"{name}.png"
    env = {**os.environ, "GSK_RENDERER": "cairo", "GDK_SCALE": "1", "GDK_DPI_SCALE": "1", "TZ": "UTC"}
    subprocess.run([sys.executable, "-m", "tests.gui.style_preview", theme, str(width), str(output)],
                   cwd=_ROOT, env=env, timeout=15, check=True)
    difference = pixel_difference(_BASE / f"{name}.png", output)
    assert difference <= 0.001, f"{difference:.3%} channels differ; candidate: {output}"


def test_pixel_comparison_rejects_changed_layout(tmp_path: Path) -> None:
    source = _BASE / "light.png"
    pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(source))
    assert pixbuf is not None
    changed = pixbuf.copy()
    pixbuf.copy_area(0, 0, pixbuf.get_width() - 24, pixbuf.get_height(), changed, 24, 0)
    target = tmp_path / "changed.png"
    changed.savev(str(target), "png", [], [])
    assert pixel_difference(source, target) > 0.001


def test_stylesheet_parses_on_the_supported_gtk() -> None:
    errors: list[str] = []
    provider = Gtk.CssProvider()
    _ = provider.connect("parsing-error", lambda _p, _s, error: errors.append(str(error)))
    provider.load_from_string(stylesheet())
    assert errors == []


@pytest.mark.parametrize(("risks", "edit", "severity"), [
    ({RiskTag.NETWORK}, False, "info"),
    ({RiskTag.OUTSIDE_WORKTREE}, False, "warning"),
    ({RiskTag.DESTRUCTIVE}, True, "warning"),
    ({RiskTag.DESTRUCTIVE}, False, "danger"),
    ({RiskTag.NETWORK, RiskTag.PUBLISH}, False, "danger"),
    ({RiskTag.DESTRUCTIVE, RiskTag.SECRETS}, True, "danger"),
    ({RiskTag.PRIVILEGE}, False, "danger"),
])
def test_risk_badge_uses_the_most_severe_risk(risks: set[RiskTag], *, edit: bool, severity: str) -> None:
    source = demo_snapshot().approvals[0]
    view = msgspec.structs.replace(source, risk=frozenset(risks),
        detail=EditDetail(files=(".codex/config.toml",), diff_preview=None) if edit else source.detail)
    # No decisions are made; this test examines the actual GTK label produced by the card.
    panel = ApprovalPanel(ApprovalsViewModel(CoreClient("/unused.sock")), on_toast=lambda _text: None,
        on_open=lambda _tab: None, on_left=lambda: None)
    card = ApprovalCard(view, "DES-3", panel)
    badge = card._risks()
    assert badge is not None
    assert badge.has_css_class(f"ac-risk-{severity}")
    assert badge.get_label()
    assert badge.get_wrap()


def test_packaged_stylesheet_works_without_the_checkout(tmp_path: Path) -> None:
    from tests.test_packaging import _builder

    site = tmp_path / "site"
    _builder()._copy_app(site)
    code = """import sys
from pathlib import Path
from aconsole.ui import style
assert Path(style.__file__).is_relative_to(Path(sys.argv[1]))
css = style.stylesheet()
assert '$' not in css
assert '.ac-risk-warning' in css
"""
    subprocess.run([sys.executable, "-c", code, str(site)], cwd=tmp_path,
                   env={**os.environ, "PYTHONPATH": str(site)}, check=True, timeout=15)
