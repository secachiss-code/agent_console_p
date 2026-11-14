# ruff: noqa: RUF100
# Standalone evidence runner; dynamic protocol fixtures and bounded subprocesses are intentional.
# ruff: noqa: ANN001, ANN201, ANN202, E402, I001, INP001, SLF001
# Standalone native CLI evidence fixture; dynamic JSON and process calls are intentional.
"""Snapshot the policy explanation with explicit fake capability evidence."""

import sys
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from aconsole.contracts.common import AgentKind  # noqa: E402
from aconsole.ui.views.setup.preferences import PreferencesDialog  # noqa: E402
from aconsole.ui.views.setup.messages import _ as translate  # noqa: E402
from aconsole.ui.viewmodels.logic.approval_modes import harness_effect_key  # noqa: E402
from ui_snapshot import main  # noqa: E402

original_show = PreferencesDialog._show_harness


def show(dialog):
    original_show(dialog)

    def focus():
        row = dialog.harness_rows[AgentKind.CLAUDE]
        row.set_subtitle(translate(harness_effect_key(frozenset({"hooks", "approval_policy"}))))
        dialog.harness_rows[AgentKind.CODEX].set_subtitle(translate(harness_effect_key(frozenset({"approval_policy_codex"}))))
        dialog.harness_rows[AgentKind.OPENCODE].set_subtitle(translate(harness_effect_key(frozenset({"approval_policy_static"}))))
        scroll = row.get_ancestor(Gtk.ScrolledWindow)
        if scroll is not None:
            adjustment = scroll.get_vadjustment()
            adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(150, focus)


PreferencesDialog._show_harness = show
raise SystemExit(main(sys.argv[1:]))
