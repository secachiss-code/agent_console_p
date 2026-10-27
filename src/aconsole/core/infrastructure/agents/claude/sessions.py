"""Claude session metadata from ``~/.claude/projects/<project>/*.jsonl``.

Only the id, timestamps, cwd, branch, and the first line of the first user prompt are kept.
Assistant lines are not decoded. Reading stops at the first prompt line, and never past a short head.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import msgspec

from aconsole.contracts.app import SessionInfo, SessionListResult

_MAX_LINES: Final = 32
_TITLE: Final = 200
_ASSISTANT: Final = (b'"type":"assistant"', b'"type": "assistant"')


def project_key(root: str) -> str:
    """Claude's project directory: ``/`` becomes ``-``, so ``/`` is ``-`` and ``/home/app`` is ``-home-app``."""
    text = root.rstrip("/") or "/"
    return text.replace("/", "-")


def resume_argv(session_id: str | None, *, fork: bool) -> tuple[str, ...]:
    """``claude --resume <id> --fork-session``. Either flag is omitted when it was not asked for."""
    if session_id is None and not fork:
        return ("claude",)
    command = ["claude"]
    if session_id is not None:
        command.extend(("--resume", session_id))
    if fork:
        command.append("--fork-session")
    return tuple(command)


def list_sessions(home: Path, project_root: str) -> SessionListResult:
    """Newest session first. A missing project directory is an empty list."""
    directory = home / ".claude" / "projects" / project_key(project_root)
    if directory.is_symlink() or not directory.is_dir():
        return SessionListResult(sessions=())
    found = [info for path in sorted(directory.glob("*.jsonl")) if (info := read_session(path)) is not None]
    found.sort(key=_order, reverse=True)
    return SessionListResult(sessions=tuple(found))


def read_session(path: Path) -> SessionInfo | None:
    """One transcript. None when the path is a symlink or has no session id."""
    if path.is_symlink() or not path.is_file():
        return None
    if path.stem == "":
        return None
    head = _head(path)
    updated = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    opened = head.started or updated
    title = head.preview if head.preview is not None else head.session_id
    preview = None if head.preview is None else head.preview[:_TITLE]
    return SessionInfo(
        session_id=head.session_id,
        title=title[:_TITLE],
        started_at=opened,
        updated_at=updated,
        cwd=head.cwd,
        branch=head.branch,
        preview=preview,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class _Head:
    session_id: str
    started: datetime | None
    cwd: str
    branch: str | None
    preview: str | None


def _order(info: SessionInfo) -> tuple[datetime, str]:
    return info.updated_at, info.session_id


def _head(path: Path) -> _Head:
    session_id = path.stem
    started: datetime | None = None
    cwd = ""
    branch: str | None = None
    preview: str | None = None
    with path.open("rb") as handle:
        for index, line in enumerate(handle):
            if index >= _MAX_LINES:
                break
            if line.strip() == b"" or _assistant(line):
                continue
            record = _object(line)
            if record is None:
                continue
            session_id = _text(record.get("sessionId")) or _text(record.get("session_id")) or session_id
            if started is None:
                started = _when(record.get("timestamp"))
            if cwd == "":
                cwd = _text(record.get("cwd")) or ""
            if branch is None:
                branch = _text(record.get("gitBranch")) or _text(record.get("git_branch"))
            if not _user(record):
                continue
            preview = _first_line(_prompt(record.get("message")))
            break
    return _Head(session_id=session_id, started=started, cwd=cwd, branch=branch, preview=preview)


def _assistant(line: bytes) -> bool:
    return any(mark in line for mark in _ASSISTANT)


def _object(line: bytes) -> dict[str, msgspec.Raw] | None:
    try:
        return msgspec.json.decode(line, type=dict[str, msgspec.Raw])
    except msgspec.DecodeError:
        return None


def _text(raw: msgspec.Raw | None) -> str | None:
    if raw is None:
        return None
    try:
        value = msgspec.json.decode(raw, type=str)
    except msgspec.DecodeError:
        return None
    text = value.strip()
    return text or None


def _when(raw: msgspec.Raw | None) -> datetime | None:
    text = _text(raw)
    if text is None:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _user(record: dict[str, msgspec.Raw]) -> bool:
    if _text(record.get("type")) == "user":
        return True
    message = record.get("message")
    if message is None:
        return False
    body = _object(bytes(message))
    if body is None:
        return False
    return _text(body.get("role")) == "user"


def _prompt(raw: msgspec.Raw | None) -> str | None:
    if raw is None:
        return None
    message = _object(bytes(raw))
    if message is None:
        return None
    return _content(message.get("content"))


def _content(raw: msgspec.Raw | None) -> str | None:
    if raw is None:
        return None
    try:
        return msgspec.json.decode(raw, type=str)
    except msgspec.DecodeError:
        return _blocks(raw)


def _blocks(raw: msgspec.Raw) -> str | None:
    try:
        blocks = msgspec.json.decode(raw, type=list[dict[str, msgspec.Raw]])
    except msgspec.DecodeError:
        return None
    for block in blocks:
        text = _text(block.get("text"))
        if text is not None:
            return text
    return None


def _first_line(text: str | None) -> str | None:
    if text is None:
        return None
    line = text.split("\n", 1)[0].strip()
    return line or None
