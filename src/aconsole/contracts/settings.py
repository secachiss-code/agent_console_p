"""User settings carried by settings/get and settings/update."""

from typing import Annotated, Literal

import msgspec
from msgspec import UNSET, Meta, UnsetType

from aconsole.contracts.common import Percent


def _languages() -> frozenset[str]:
    return frozenset(("csharp", "python", "rust", "typescript"))


class Settings(msgspec.Struct, frozen=True, kw_only=True):
    rev: int
    vault_path: str
    worktrees_dir: str
    approvals_mode: Literal["inline", "native"] = "inline"
    approval_hold_seconds: Annotated[int, Meta(ge=30, le=540)] = 540
    notify_when: Literal["unfocused", "always", "never"] = "unfocused"
    terminal_font: str | None = None
    terminal_font_size: Annotated[int, Meta(ge=8, le=32)] = 11
    scrollback_lines: Annotated[int, Meta(ge=1000, le=200000)] = 20000
    theme: Literal["system", "light", "dark"] = "system"
    quota_warn_pct: Percent = 80
    quota_critical_pct: Percent = 95
    checkpoints_enabled: bool = True
    checkpoints_keep: Annotated[int, Meta(ge=5, le=500)] = 100
    keymap_overrides: dict[str, str] = msgspec.field(default_factory=dict)
    codegraph_languages: frozenset[str] = msgspec.field(default_factory=_languages)
    setup_completed: bool = False


class SettingsPatch(msgspec.Struct, frozen=True, kw_only=True):
    vault_path: str | UnsetType = UNSET
    worktrees_dir: str | UnsetType = UNSET
    approvals_mode: Literal["inline", "native"] | UnsetType = UNSET
    approval_hold_seconds: Annotated[int, Meta(ge=30, le=540)] | UnsetType = UNSET
    notify_when: Literal["unfocused", "always", "never"] | UnsetType = UNSET
    terminal_font: str | UnsetType | None = UNSET
    terminal_font_size: Annotated[int, Meta(ge=8, le=32)] | UnsetType = UNSET
    scrollback_lines: Annotated[int, Meta(ge=1000, le=200000)] | UnsetType = UNSET
    theme: Literal["system", "light", "dark"] | UnsetType = UNSET
    quota_warn_pct: Percent | UnsetType = UNSET
    quota_critical_pct: Percent | UnsetType = UNSET
    checkpoints_enabled: bool | UnsetType = UNSET
    checkpoints_keep: Annotated[int, Meta(ge=5, le=500)] | UnsetType = UNSET
    keymap_overrides: dict[str, str] | UnsetType = UNSET
    codegraph_languages: frozenset[str] | UnsetType = UNSET
    setup_completed: bool | UnsetType = UNSET


def _keep[T](incoming: T | UnsetType, current: T) -> T:
    if isinstance(incoming, UnsetType):
        return current
    return incoming


def commit_settings(current: Settings, patch: SettingsPatch) -> Settings:
    """Apply `patch`. The same values leave `rev` unchanged."""
    merged = msgspec.structs.replace(
        current,
        vault_path=_keep(patch.vault_path, current.vault_path),
        worktrees_dir=_keep(patch.worktrees_dir, current.worktrees_dir),
        approvals_mode=_keep(patch.approvals_mode, current.approvals_mode),
        approval_hold_seconds=_keep(patch.approval_hold_seconds, current.approval_hold_seconds),
        notify_when=_keep(patch.notify_when, current.notify_when),
        terminal_font=_keep(patch.terminal_font, current.terminal_font),
        terminal_font_size=_keep(patch.terminal_font_size, current.terminal_font_size),
        scrollback_lines=_keep(patch.scrollback_lines, current.scrollback_lines),
        theme=_keep(patch.theme, current.theme),
        quota_warn_pct=_keep(patch.quota_warn_pct, current.quota_warn_pct),
        quota_critical_pct=_keep(patch.quota_critical_pct, current.quota_critical_pct),
        checkpoints_enabled=_keep(patch.checkpoints_enabled, current.checkpoints_enabled),
        checkpoints_keep=_keep(patch.checkpoints_keep, current.checkpoints_keep),
        keymap_overrides=_keep(patch.keymap_overrides, current.keymap_overrides),
        codegraph_languages=_keep(patch.codegraph_languages, current.codegraph_languages),
        setup_completed=_keep(patch.setup_completed, current.setup_completed),
    )
    if merged == current:
        return current
    return msgspec.structs.replace(merged, rev=current.rev + 1)
