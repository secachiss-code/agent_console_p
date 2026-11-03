"""Palette entries from files agents read themselves: OpenCode commands and Cursor rules."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from aconsole.contracts.app import PaletteItem
from aconsole.contracts.common import PaletteKind

_HEAD: Final = 4096
_USER: Final = "user"
_PROJECT: Final = "project"


def opencode_commands(home: Path, project: Path) -> tuple[PaletteItem, ...]:
    """`~/.config/opencode/commands/*.md`, then the project's `.opencode/commands/*.md`."""
    return (
        *_scan(home / ".config" / "opencode" / "commands", "*.md", _USER),
        *_scan(project / ".opencode" / "commands", "*.md", _PROJECT),
    )


def cursor_rules(project: Path) -> tuple[PaletteItem, ...]:
    """The project's `.cursor/rules/*.mdc`."""
    return _scan(project / ".cursor" / "rules", "*.mdc", _PROJECT)


def _scan(root: Path, pattern: str, scope: str) -> tuple[PaletteItem, ...]:
    if root.is_symlink() or not root.is_dir():
        return ()
    return tuple(
        _item(path, scope) for path in sorted(root.glob(pattern)) if path.is_file() and not path.is_symlink()
    )


def _item(path: Path, scope: str) -> PaletteItem:
    name = path.stem
    text = path.read_text(encoding="utf-8", errors="replace")[:_HEAD]
    return PaletteItem(
        id=f"native-skill:{scope}:{name}",
        kind=PaletteKind.NATIVE_SKILL,
        title=name,
        subtitle=_description(text),
        keywords=(name,),
        aliases=(),
        score_hint=0.0,
        enabled=True,
        disabled_reason=None,
        argument=None,
        shortcut=None,
    )


def _description(text: str) -> str | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    for line in text[3:max(end, 0)].splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() == "description":
            return value.strip().strip("\"'") or None
    return None
