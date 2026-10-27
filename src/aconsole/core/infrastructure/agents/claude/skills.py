"""Claude skills from ``~/.claude/skills`` and the project's ``.claude/skills``.

The result is cached until a skill file or its root directory changes mtime.
Only ``SKILL.md`` front matter and the first heading are read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, final

from aconsole.contracts.app import PaletteItem
from aconsole.contracts.common import PaletteKind

_HEAD: Final = 4096
_USER: Final = "user"
_PROJECT: Final = "project"
_KIND: Final = PaletteKind.NATIVE_SKILL


@final
class SkillDiscovery:
    """Palette entries for native skills. A changed mtime drops the cached tuple."""

    _cached: tuple[PaletteItem, ...] | None
    _stamp: tuple[tuple[str, int], ...] | None

    def __init__(self) -> None:
        self._cached = None
        self._stamp = None

    def discover(self, home: Path, project: Path) -> tuple[PaletteItem, ...]:
        """User skills first, then project skills. The same tuple is returned while mtimes hold."""
        roots = ((home / ".claude" / "skills", _USER), (project / ".claude" / "skills", _PROJECT))
        stamp = _stamp(roots)
        cached = self._cached
        if cached is not None and stamp == self._stamp:
            return cached
        found = tuple(item for root, scope in roots for item in _skills(root, scope))
        self._cached = found
        self._stamp = stamp
        return found


def _stamp(roots: tuple[tuple[Path, str], ...]) -> tuple[tuple[str, int], ...]:
    marks: list[tuple[str, int]] = []
    for root, _scope in roots:
        marks.append((str(root), _mtime(root)))
        if root.is_symlink() or not root.is_dir():
            continue
        for skill in sorted(root.glob("*/SKILL.md")):
            if skill.is_symlink():
                continue
            marks.append((str(skill), _mtime(skill)))
    return tuple(marks)


def _skills(root: Path, scope: str) -> tuple[PaletteItem, ...]:
    if root.is_symlink() or not root.is_dir():
        return ()
    found: list[PaletteItem] = []
    for skill in sorted(root.glob("*/SKILL.md")):
        if skill.is_symlink() or not skill.is_file():
            continue
        item = _item(skill, scope)
        if item is not None:
            found.append(item)
    return tuple(found)


def _item(skill: Path, scope: str) -> PaletteItem | None:
    name = skill.parent.name
    if name == "":
        return None
    text = skill.read_text(encoding="utf-8", errors="replace")[:_HEAD]
    meta = _frontmatter(text)
    title = meta.get("name") or name
    description = meta.get("description") or _heading(text)
    keywords = (title,) if title == name else (title, name)
    return PaletteItem(
        id=f"native-skill:{scope}:{name}",
        kind=_KIND,
        title=title,
        subtitle=description,
        keywords=keywords,
        aliases=(),
        score_hint=0.0,
        enabled=True,
        disabled_reason=None,
        argument=None,
        shortcut=None,
    )


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    found: dict[str, str] = {}
    for line in text[3:end].splitlines():
        key, separator, value = line.partition(":")
        if separator == "" or key.strip() == "":
            continue
        found[key.strip()] = value.strip().strip("\"'")
    return found


def _heading(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            return title or None
    return None


def _mtime(path: Path) -> int:
    if path.is_symlink() or not path.exists():
        return 0
    return path.stat().st_mtime_ns
