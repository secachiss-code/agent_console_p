"""Write vault notes with ruamel, an atomic rename, and a short echo journal.

A second write of the same bytes does not touch the file. A user-owned section
is never replaced. App lines are inserted under their heading. The same blake2b
note digests support both the short journal and durable publication records.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from threading import Lock
from typing import Protocol

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

_USER_SECTIONS = frozenset({"Цель"})
_TTL = timedelta(seconds=10)
_DIGEST = 32
_FENCES = 3
_MAX_ATTEMPTS = 4
_MARKDOWN_INDENT = 3
_MARKDOWN_FENCE = 3


class ConcurrentNoteEditError(Exception):
    """The file changed after the publisher read it."""


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


def dump_front(values: dict[str, object]) -> str:
    """YAML for a note's frontmatter, without its fences."""
    return _dump(_engine(), values)


def load_front(text: str) -> dict[str, object] | None:
    """Frontmatter as a mapping. None when it is not a YAML mapping."""
    try:
        loaded = _load(_engine(), text)
    except YAMLError:
        return None
    if loaded is None:
        return {}
    if not isinstance(loaded, Mapping):
        return None
    return {str(key): value for key, value in loaded.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType] -- YAML is untyped


def split_note(text: str) -> tuple[str, str]:
    """Frontmatter without its fences, and the body. A file without fences has an empty front."""
    if text.startswith("---\n"):
        parts = text.split("---\n", 2)
        if len(parts) == _FENCES:
            return parts[1], parts[2]
    return "", text


def _digest(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=_DIGEST).hexdigest()


def note_hashes(text: str) -> tuple[str, str]:
    """Digest the exact frontmatter and body bytes, without parsing YAML."""
    front, body = split_note(text)
    return _digest(front), _digest(body)


def _attempt_section(text: str) -> tuple[int, int, int] | None:
    """Locate the app-owned H2 section, ignoring headings inside fenced examples."""
    offset = 0
    found: tuple[int, int] | None = None
    fence = ""
    width = 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        content = stripped.lstrip(" ")
        if len(stripped) - len(content) <= _MARKDOWN_INDENT:
            marker = content[:1]
            run = len(content) - len(content.lstrip(marker)) if marker in {"`", "~"} else 0
            if fence:
                if marker == fence and run >= width and not content[run:].strip():
                    fence = ""
            elif run >= _MARKDOWN_FENCE:
                fence, width = marker, run
            elif found is not None and (content in {"#", "##"} or content.startswith(("# ", "## ", "#\t", "##\t"))):
                return *found, offset
            elif content == "## Попытки":
                found = offset, offset + len(line)
        offset += len(line)
    return None if found is None else (*found, len(text))


def protected_body_hash(text: str) -> str:
    """Hash exact body bytes outside the app's attempt section, preserving line endings."""
    _front, body = split_note(text)
    section = _attempt_section(body)
    if section is not None:
        start, _content, end = section
        body = body[:start] + body[end:]
    return _digest(body)


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
        self._lock: Lock = Lock()

    def remember(self, path: Path, text: str, now: datetime) -> None:
        """Record the hashes of ``text`` until ``now + ttl``."""
        with self._lock:
            self._prune(now)
            front, body = split_note(text)
            self._seen[path] = _Echo(front=_digest(front), body=_digest(body), until=now + self._ttl)

    def is_echo(self, path: Path, text: str, now: datetime) -> bool:
        """True when ``text`` is a write this book still remembers."""
        with self._lock:
            self._prune(now)
            found = self._seen.get(path)
            if found is None:
                return False
            front, body = split_note(text)
            return found.front == _digest(front) and found.body == _digest(body)

    def __len__(self) -> int:
        with self._lock:
            return len(self._seen)

    def _prune(self, now: datetime) -> None:
        stale = [path for path, item in self._seen.items() if now > item.until]
        for path in stale:
            del self._seen[path]


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


def update_attempt(text: str, attempt_no: int, line: str) -> str:
    """Upsert one attempt line, preserving the rest of the user-owned note body."""
    if not 1 <= attempt_no <= _MAX_ATTEMPTS or "\n" in line or "\r" in line:
        message = "attempt journal requires one line and an attempt number in 1..4"
        raise ValueError(message)
    front, body = split_note(text)
    section = _attempt_section(body)
    if section is None:
        suffix = "" if text.endswith("\n") else "\n"
        return f"{text}{suffix}## Попытки\n{line}\n"
    _start, body_at, end = section
    prefix = f"- {attempt_no}/4 · "
    lines = body[body_at:end].splitlines(keepends=True)
    for index, old in enumerate(lines):
        if old.startswith(prefix):
            lines[index] = line + ("\n" if old.endswith("\n") else "")
            break
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(line + "\n")
    updated = body[:body_at] + "".join(lines) + body[end:]
    return f"---\n{front}---\n{updated}" if text.startswith("---\n") else updated


class VaultWriter:
    """Atomic writes under one vault root. Unchanged bytes leave mtime alone."""

    def __init__(self, echo: EchoBook | None = None, now: Callable[[], datetime] | None = None) -> None:
        self.echo: EchoBook = EchoBook() if echo is None else echo
        self._now: Callable[[], datetime] = _clock if now is None else now

    def is_echo(self, path: Path, text: str) -> bool:
        """Whether ``text`` is what this writer just put at ``path``."""
        return self.echo.is_echo(path, text, self._now())

    def write(self, path: Path, text: str, *, expected: str | None = None) -> bool:
        """Replace ``path`` when the bytes differ. Return whether the file changed."""
        data = text.encode("utf-8")
        current = path.read_bytes() if path.is_file() else None
        if expected is not None and current != expected.encode("utf-8"):
            raise ConcurrentNoteEditError(str(path))
        if current == data:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.aconsole-tmp")
        _ = temporary.write_bytes(data)
        _ = temporary.replace(path)
        self.echo.remember(path, text, self._now())
        return True


def _clock() -> datetime:
    return datetime.now(UTC)
