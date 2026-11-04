"""B1 keyboard binding resolution and settings overrides."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from aconsole.ui.viewmodels.logic.keymap import BindingSpec


def test_b1_keymap_defaults_and_overrides() -> None:
    """`keymap_overrides` from settings replace, disable, and validate defaults."""
    from aconsole.ui.client.core_client import CoreClient
    from aconsole.ui.viewmodels.app_vm import AppViewModel
    from aconsole.ui.viewmodels.logic.keymap import DEFAULT_BINDINGS, resolve_keymap

    defaults = {binding.action_id: binding.accelerators for binding in DEFAULT_BINDINGS}
    expected = {
        "palette": ("<Control><Shift>p",),
        "new-tab": ("<Control><Shift>t",),
        "close-tab": ("<Control><Shift>w",),
        "next-tab": ("<Control>Page_Down", "<Control>Tab"),
        "previous-tab": ("<Control>Page_Up", "<Control><Shift>Tab"),
        **{f"tab-{number}": (f"<Alt>{number}",) for number in range(1, 10)},
        "next-attention": ("<Control><Shift>y",),
        "attention-next": ("j",),
        "attention-previous": ("k",),
        "attention-allow": ("a",),
        "attention-deny": ("d",),
        "attention-open": ("Return",),
        "approve": ("<Control><Shift>Return",),
        "deny": ("<Control><Shift>BackSpace",),
        "diff": ("<Control><Shift>d",),
        "terminal-search": ("<Control><Shift>f",),
        "copy": ("<Control><Shift>c",),
        "paste": ("<Control><Shift>v",),
        "toggle-sidebar": ("F9", "<Control><Shift>b"),
        "settings": ("<Control>comma",),
        "shortcuts": ("<Control>question", "F1"),
        "zoom-in": ("<Control>equal",),
        "zoom-reset": ("<Control>0",),
        "zoom-out": ("<Control><Alt>minus",),
        "quit": ("<Control>q",),
        "send-next-key": ("<Control><Shift><Alt>k",),
    }
    assert {action: defaults[action] for action in expected} == expected

    # Attention-card controls are present but are scoped to that UI, not global terminal capture.
    by_action = {binding.action_id: binding for binding in DEFAULT_BINDINGS}
    for action in ("attention-next", "attention-previous", "attention-allow", "attention-deny", "attention-open"):
        assert action in by_action
        assert by_action[action].context != by_action["palette"].context

    replaced = resolve_keymap({"palette": "<Control><Alt>p"})
    replaced_by_action = {binding.action_id: binding.accelerators for binding in replaced}
    assert replaced_by_action["palette"] == ("<Control><Alt>p",)
    assert replaced_by_action["new-tab"] == defaults["new-tab"]

    disabled = resolve_keymap({"palette": ""})
    assert "palette" not in {binding.action_id for binding in disabled}

    vm = AppViewModel(CoreClient("unused"))
    assert vm.apply_overrides({"palette": "<Control><Alt>p"}) is True
    assert {binding.action_id: binding.accelerators for binding in vm.bindings}["palette"] == (
        "<Control><Alt>p",
    )

    accepted = vm.bindings
    assert vm.apply_overrides({"not-an-action": "<Control>x"}) is False
    assert vm.bindings == accepted
    assert vm.apply_overrides({"palette": "<Control>x", "new-tab": "<Control>x"}) is False
    assert vm.bindings == accepted

    with pytest.raises(ValueError, match=r".+"):
        resolve_keymap({"not-an-action": "<Control>x"})
    with pytest.raises(ValueError, match=r".+"):
        resolve_keymap({"palette": "<Control>x", "new-tab": "<Control>x"})


def test_b1_keymap_rejects_global_and_attention_context_collision() -> None:
    # Global and attention-local shortcuts may not collide.
    from aconsole.ui.viewmodels.logic.keymap import resolve_keymap

    with pytest.raises(ValueError, match="collision"):
        resolve_keymap({"palette": "j"})


def test_b1_keymap_override_disables_all_alternatives() -> None:
    # An empty override disables every default accelerator for the action.
    from aconsole.ui.viewmodels.logic.keymap import DEFAULT_BINDINGS, resolve_keymap

    sidebar = next(binding for binding in DEFAULT_BINDINGS if binding.action_id == "toggle-sidebar")
    assert len(sidebar.accelerators) > 1
    resolved = resolve_keymap({"toggle-sidebar": ""})
    assert "toggle-sidebar" not in {binding.action_id for binding in resolved}


def test_b1_app_vm_rejects_binding_callback_atomically_and_dispatches() -> None:
    # Failed binding replacement preserves the last valid map; dispatch notifies both handlers.
    from aconsole.ui.client.core_client import CoreClient
    from aconsole.ui.viewmodels.app_vm import AppViewModel

    vm = AppViewModel(CoreClient("unused"))
    before = vm.bindings
    candidates: list[tuple[BindingSpec, ...]] = []

    def reject_bindings(candidate: tuple[BindingSpec, ...]) -> bool:
        candidates.append(candidate)
        return False

    vm.on_bindings = reject_bindings
    assert vm.apply_overrides({"palette": "<Control><Alt>p"}) is False
    assert vm.bindings == before
    assert len(candidates) == 1
    assert {binding.action_id: binding.accelerators for binding in candidates[0]}["palette"] == (
        "<Control><Alt>p",
    )

    delivered: list[tuple[str, str]] = []


    def handle(action_id: str) -> bool:
        delivered.append(("handler", action_id))
        return True

    vm.action_handler = handle
    vm.on_action = lambda action_id: delivered.append(("action", action_id))
    assert vm.dispatch("palette") is True
    assert delivered == [("handler", "palette"), ("action", "palette")]


def test_action_without_a_handler_leaves_the_key_alone() -> None:
    from aconsole.ui.client.core_client import CoreClient
    from aconsole.ui.viewmodels.app_vm import AppViewModel

    vm = AppViewModel(CoreClient("unused"))
    assert vm.dispatch("palette") is False
    vm.action_handler = lambda _action: False
    assert vm.dispatch("palette") is False


def test_a_saved_override_for_a_retired_action_keeps_the_others() -> None:
    from aconsole.ui.viewmodels.logic.keymap import resolve_keymap

    resolved = {item.action_id: item.accelerators for item in resolve_keymap({
        "handover": "<Control><Shift>h", "palette": "<Control><Alt>p",
    })}
    assert "handover" not in resolved
    assert resolved["palette"] == ("<Control><Alt>p",)


def test_zoom_stays_in_range_without_drift() -> None:
    from aconsole.ui.viewmodels.logic.fonts import zoomed

    assert zoomed(1.0, 1) == 1.1
    assert zoomed(1.1, -1) == 1.0
    assert zoomed(3.0, 1) == 3.0
    assert zoomed(0.5, -1) == 0.5
