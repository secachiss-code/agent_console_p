"""OpenCode sessions, asked from a running server: `GET /session`. Children of a session are subagents, not shown."""

from __future__ import annotations

import base64
import http.client
from datetime import UTC, datetime
from typing import Final

import msgspec

from aconsole.contracts.app import SessionInfo, SessionListResult

_HOST: Final = "127.0.0.1"
_USER: Final = "opencode"
_OK: Final = 200
_TIMEOUT_S: Final = 5.0
_TITLE: Final = 200


class _Time(msgspec.Struct, frozen=True):
    created: int = 0
    updated: int = 0


class _Session(msgspec.Struct, frozen=True):
    id: str
    directory: str = ""
    title: str = ""
    parentID: str | None = None  # noqa: N815 -- OpenCode's own name
    time: _Time = _Time()


def parse_sessions(body: bytes, project_root: str) -> SessionListResult:
    """Top-level sessions of `project_root`, newest first. A body that does not parse is an empty list."""
    try:
        rows = msgspec.json.decode(body, type=list[_Session])
    except msgspec.DecodeError:
        return SessionListResult(sessions=())
    root = project_root.rstrip("/") or "/"
    found = [_info(row) for row in rows if not row.parentID and (row.directory.rstrip("/") or "/") == root]
    found.sort(key=lambda info: (info.updated_at, info.session_id), reverse=True)
    return SessionListResult(sessions=tuple(found))


def list_sessions(port: int, token: str, project_root: str) -> SessionListResult:
    """What the tab's server knows. A server that does not answer is an empty list."""
    auth = base64.b64encode(f"{_USER}:{token}".encode()).decode()
    connection = http.client.HTTPConnection(_HOST, port, timeout=_TIMEOUT_S)
    try:
        connection.request("GET", "/session", headers={"Authorization": f"Basic {auth}"})
        response = connection.getresponse()
        if response.status != _OK:
            return SessionListResult(sessions=())
        return parse_sessions(response.read(), project_root)
    except (OSError, http.client.HTTPException):
        return SessionListResult(sessions=())
    finally:
        connection.close()


def _info(row: _Session) -> SessionInfo:
    title = row.title.split("\n", 1)[0].strip()[:_TITLE]
    return SessionInfo(
        session_id=row.id,
        title=title or row.id,
        started_at=_when(row.time.created),
        updated_at=_when(row.time.updated or row.time.created),
        cwd=row.directory,
        branch=None,
        preview=title or None,
    )


def _when(milliseconds: int) -> datetime:
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)
