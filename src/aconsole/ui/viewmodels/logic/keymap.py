"""Default shortcuts and reversible user overrides, independent of GTK."""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BindingSpec:
    action_id: str
    accelerators: tuple[str, ...]
    context: str = "global"


DEFAULT_BINDINGS: tuple[BindingSpec, ...] = (
    BindingSpec("palette", ("<Control><Shift>p",)),
    BindingSpec("new-tab", ("<Control><Shift>t",)),
    BindingSpec("close-tab", ("<Control><Shift>w",)),
    BindingSpec("next-tab", ("<Control>Page_Down", "<Control>Tab")),
    BindingSpec("previous-tab", ("<Control>Page_Up", "<Control><Shift>Tab")),
    *(BindingSpec(f"tab-{number}", (f"<Alt>{number}",)) for number in range(1, 10)),
    BindingSpec("next-attention", ("<Control><Shift>y",)),
    BindingSpec("approve", ("<Control><Shift>Return",), "attention"),
    BindingSpec("deny", ("<Control><Shift>BackSpace",), "attention"),
    BindingSpec("attention-next", ("j",), "attention"),
    BindingSpec("attention-previous", ("k",), "attention"),
    BindingSpec("attention-allow", ("a",), "attention"),
    BindingSpec("attention-deny", ("d",), "attention"),
    BindingSpec("attention-open", ("Return",), "attention"),
    BindingSpec("handover", ("<Control><Shift>h",)),
    BindingSpec("diff", ("<Control><Shift>d",)),
    BindingSpec("terminal-search", ("<Control><Shift>f",)),
    BindingSpec("copy", ("<Control><Shift>c",)),
    BindingSpec("paste", ("<Control><Shift>v",)),
    BindingSpec("toggle-sidebar", ("F9", "<Control><Shift>b")),
    BindingSpec("settings", ("<Control>comma",)),
    BindingSpec("shortcuts", ("<Control>question", "F1")),
    BindingSpec("zoom-in", ("<Control>equal",)),
    BindingSpec("zoom-reset", ("<Control>0",)),
    BindingSpec("zoom-out", ("<Control><Alt>minus",)),
    BindingSpec("quit", ("<Control>q",)),
    BindingSpec("send-next-key", ("<Control><Shift><Alt>k",)),
)


def resolve_keymap(overrides: Mapping[str, str]) -> tuple[BindingSpec, ...]:
    """Replace an action's bindings atomically; reject unknown IDs and collisions."""
    unknown = set(overrides).difference(item.action_id for item in DEFAULT_BINDINGS)
    if unknown:
        message = f"unknown shortcut action: {min(unknown)}"
        raise ValueError(message)
    result: list[BindingSpec] = []
    seen: dict[str, str] = {}
    for binding in DEFAULT_BINDINGS:
        value = overrides.get(binding.action_id)
        accelerators = binding.accelerators if value is None else ((value,) if value else ())
        if not accelerators:
            continue
        for accelerator in accelerators:
            key = accelerator.casefold()
            previous = seen.get(key)
            if previous is not None and (previous == binding.context or "global" in (previous, binding.context)):
                message = f"shortcut collision: {accelerator}"
                raise ValueError(message)
            seen[key] = binding.context
        result.append(BindingSpec(binding.action_id, accelerators, binding.context))
    return tuple(result)
