"""GTK shortcut routing: leave all other keys to the focused widget."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from aconsole.ui.viewmodels.app_vm import AppViewModel  # noqa: E402
from aconsole.ui.viewmodels.logic.keymap import BindingSpec  # noqa: E402

_MODIFIERS = frozenset((
    Gdk.KEY_Control_L, Gdk.KEY_Control_R, Gdk.KEY_Shift_L, Gdk.KEY_Shift_R,
    Gdk.KEY_Alt_L, Gdk.KEY_Alt_R, Gdk.KEY_Meta_L, Gdk.KEY_Meta_R,
    Gdk.KEY_Super_L, Gdk.KEY_Super_R, Gdk.KEY_Hyper_L, Gdk.KEY_Hyper_R,
    Gdk.KEY_Caps_Lock, Gdk.KEY_Shift_Lock,
))


class ShortcutManager:
    def __init__(self, window: Gtk.Window, viewmodel: AppViewModel) -> None:
        self.window: Gtk.Window = window
        self.viewmodel: AppViewModel = viewmodel
        self.controller: Gtk.ShortcutController = Gtk.ShortcutController.new()
        self.controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.controller.set_scope(Gtk.ShortcutScope.LOCAL)
        self.window.add_controller(self.controller)
        self._key_controller: Gtk.EventControllerKey = Gtk.EventControllerKey.new()
        self._key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        _ = self._key_controller.connect("key-pressed", self._key_pressed)
        self.window.add_controller(self._key_controller)
        self._focus_handler: int = self.window.connect("notify::is-active", self._active_changed)
        self._shortcuts: list[Gtk.Shortcut] = []
        self._bypass: bool = False
        self.viewmodel.on_bindings = self.apply_bindings
        _ = self.apply_bindings(self.viewmodel.bindings)

    def apply_bindings(self, bindings: tuple[BindingSpec, ...]) -> bool:
        candidate: list[Gtk.Shortcut] = []
        seen: dict[str, str] = {}
        for binding in bindings:
            for accelerator in binding.accelerators:
                trigger = Gtk.ShortcutTrigger.parse_string(accelerator)
                if trigger is None:
                    return False
                key = trigger.to_string()
                previous = seen.get(key)
                if previous is not None and (previous == binding.context or "global" in (previous, binding.context)):
                    return False
                seen[key] = binding.context
                candidate.append(Gtk.Shortcut.new(trigger, self._make_action(binding)))
        for shortcut in self._shortcuts:
            self.controller.remove_shortcut(shortcut)
        for shortcut in candidate:
            self.controller.add_shortcut(shortcut)
        self._shortcuts = candidate
        return True

    def _make_action(self, binding: BindingSpec) -> Gtk.CallbackAction:
        def activate(_widget: Gtk.Widget, _args: GLib.Variant | None) -> bool:
            if binding.context != "global" and not self._attention_focused():
                return False
            if binding.action_id == "send-next-key":
                self._bypass = True
                self.controller.set_propagation_phase(Gtk.PropagationPhase.NONE)
            self.viewmodel.dispatch(binding.action_id)
            return True

        return Gtk.CallbackAction.new(activate)

    def _attention_focused(self) -> bool:
        focus = self.window.get_focus()
        while focus is not None:
            if focus.has_css_class("ac-attention-card"):
                return True
            focus = focus.get_parent()
        return False

    def _key_pressed(
        self, _controller: Gtk.EventControllerKey, keyval: int, _keycode: int, _state: Gdk.ModifierType,
    ) -> bool:
        if self._bypass and keyval not in _MODIFIERS:
            self._bypass = False
            _ = GLib.idle_add(self._restore_capture)
        return False

    def _restore_capture(self) -> bool:
        self.controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        return GLib.SOURCE_REMOVE

    def _active_changed(self, window: Gtk.Window, _pspec: object) -> None:
        if not window.is_active() and self._bypass:
            self._bypass = False
            _ = self._restore_capture()

    def close(self) -> None:
        self.viewmodel.on_bindings = None
        self.window.disconnect(self._focus_handler)
        self.window.remove_controller(self.controller)
        self.window.remove_controller(self._key_controller)
