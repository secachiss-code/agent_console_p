"""Write vault notes with ruamel, an atomic rename, and a short echo journal.

A second write of the same bytes does not touch the file. A user-owned section
is never replaced. App lines are inserted under their heading.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Protocol

from ruamel.yaml import YAML

_USER_SECTIONS = frozenset({"Цель"})
_TTL = timedelta(seconds=10)
_DIGEST = 32
_FENCES = 3


class UserSectionError(Exception):
    """The caller asked the app to rewrite a section the user owns."""


def _engine() -> YAML:
    engine = YAML(typ="rt")
    engine.preserve_quotes = True
    engine.indent(mapping=2, sequence=4, offset=2)  # pyright: ignore[reportAny] -- ruamel types indent as Any
    engine.width = 4096
    return engine


class _YamlLoader(Protocol):
    def load(self, stream: str) -> object:
        """Load a YAML document."""
        ...


def _load(engine: _YamlLoader, text: str) -> object:
    return engine.load(text)


def _dump(engine: YAML, value: object) -> str:
    buffer = StringIO()
    engine.dump(value, buffer)  # pyright: ignore[reportUnknownMemberType] -- ruamel dump is untyped
    return buffer.getvalue()


def render(text: str) -> str:
    """Emit ``text`` again. Frontmatter and ``.base`` YAML come back byte for byte."""
    engine = _engine()
    if text.startswith("---\n"):
        parts = text.split("---\n", 2)
        if len(parts) == _FENCES:
            return f"---\n{_dump(engine, _load(engine, parts[1]))}---\n{parts[2]}"
    if _bare_yaml(text):
        loaded = _load(engine, text)
        if loaded is not None:
            return _dump(engine, loaded)
    return text


def _bare_yaml(text: str) -> bool:
    first = text.split("\n", 1)[0]
    return ":" in first and not first.startswith(("#", "{"))


def split_note(text: str) -> tuple[str, str]:
    """Frontmatter without its fences, and the body. A file without fences has an empty front."""
    if text.startswith("---\n"):
        parts = text.split("---\n", 2)
        if len(parts) == _FENCES:
            return parts[1], parts[2]
    return "", text


def _digest(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=_DIGEST).hexdigest()


@dataclass(frozen=True, slots=True)
class _Echo:
    front: str
    body: str
    until: datetime


class EchoBook:
    """Hashes of writes this process just made. A matching event inside the ttl is our own."""

    def __init__(self, ttl: timedelta = _TTL) -> None:
        self._ttl: timedelta = ttl
        self._seen: dict[Path, _Echo] = {}

    def remember(self, path: Path, text: str, now: datetime) -> None:
        """Record the hashes of ``text`` until ``now + ttl``."""
        front, body = split_note(text)
        self._seen[path] = _Echo(front=_digest(front), body=_digest(body), until=now + self._ttl)

    def is_echo(self, path: Path, text: str, now: datetime) -> bool:
        """True when ``text`` is a write this book still remembers."""
        found = self._seen.get(path)
        if found is None or now > found.until:
            return False
        front, body = split_note(text)
        return found.front == _digest(front) and found.body == _digest(body)


def append_under(text: str, heading: str, line: str) -> str:
    """Add ``line`` at the end of ``## heading``. A missing heading is created at the end.

    ``Цель`` belongs to the user. Asking to write it raises ``UserSectionError``.
    """
    if heading in _USER_SECTIONS:
        raise UserSectionError(heading)
    marker = f"## {heading}\n"
    start = text.find(marker)
    if start < 0:
        suffix = "" if text.endswith("\n") else "\n"
        return f"{text}{suffix}{marker}{line}\n"
    body_at = start + len(marker)
    nxt = text.find("\n## ", body_at)
    if nxt < 0:
        head = text[:body_at]
        section = text[body_at:]
        tail = ""
    else:
        head = text[:body_at]
        section = text[body_at : nxt + 1]
        tail = text[nxt + 1 :]
    if section and not section.endswith("\n"):
        section = f"{section}\n"
    return f"{head}{section}{line}\n{tail}"


class VaultWriter:
    """Atomic writes under one vault root. Unchanged bytes leave mtime alone."""

    def __init__(self, echo: EchoBook | None = None, now: Callable[[], datetime] | None = None) -> None:
        self.echo: EchoBook = EchoBook() if echo is None else echo
        self._now: Callable[[], datetime] = _clock if now is None else now

    def write(self, path: Path, text: str) -> bool:
        """Replace ``path`` when the bytes differ. Return whether the file changed."""
        data = text.encode("utf-8")
        if path.is_file() and path.read_bytes() == data:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.aconsole-tmp")
        _ = temporary.write_bytes(data)
        _ = temporary.replace(path)
        self.echo.remember(path, text, self._now())
        return True


def _clock() -> datetime:
    return datetime.now(UTC)
