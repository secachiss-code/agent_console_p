"""Shortcut state and application action requests without GTK dependencies."""

from collections.abc import Callable, Mapping

from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.viewmodels.logic.keymap import BindingSpec, resolve_keymap


class AppViewModel:
    def __init__(self, client: CoreClient) -> None:
        self.client: CoreClient = client
        self.bindings: tuple[BindingSpec, ...] = resolve_keymap({})
        self.on_action: Callable[[str], None] | None = None
        self.on_bindings: Callable[[tuple[BindingSpec, ...]], bool] | None = None
        self.action_handler: Callable[[str], None] | None = None

    def apply_overrides(self, overrides: Mapping[str, str]) -> bool:
        try:
            candidate = resolve_keymap(overrides)
        except ValueError:
            return False
        if self.on_bindings is not None and not self.on_bindings(candidate):
            return False
        self.bindings = candidate
        return True

    def dispatch(self, action_id: str) -> None:
        if self.action_handler is not None:
            self.action_handler(action_id)
        if self.on_action is not None:
            self.on_action(action_id)
