"""NDJSON for the sidecar. The standard library only; the core decodes with msgspec.

Notifications are dropped by the caller when the core socket is down. The
terminal must not wait on them.
"""

import json
from typing import Final

_JSONRPC: Final = "2.0"
_NOTIFICATION: Final = "notification"
_REQUEST: Final = "request"
_STARTED: Final = "sidecar/started"
_SIGNAL: Final = "sidecar/signal"
_STATS: Final = "sidecar/stats"
_EXITED: Final = "sidecar/exited"
_INITIALIZE: Final = "initialize"
_NAME: Final = "aconsole-pty"
_VERSION: Final = "0.0.0"
_ROLE: Final = "sidecar"
_MAJOR: Final = 1
_MINOR: Final = 0
_REQUEST_ID: Final = 1
_LOCALE: Final = "ru"

Param = str | int | None
Params = dict[str, Param]
Json = Params | list["Json"] | dict[str, "Json"] | str | int | None


def started(child_pid: int, started_at: str) -> bytes:
    """`sidecar/started`."""
    return _note(_STARTED, {"child_pid": child_pid, "started_at": started_at})


def signal(kind: str, text: str | None, progress: int | None) -> bytes:
    """`sidecar/signal`. Absent text and progress are omitted."""
    params: Params = {"kind": kind}
    if text is not None:
        params["text"] = text
    if progress is not None:
        params["progress"] = progress
    return _note(_SIGNAL, params)


def stats(bytes_out: int, frames: int, frames_timed_out: int, max_frame_bytes: int) -> bytes:
    """`sidecar/stats`."""
    return _note(
        _STATS,
        {
            "bytes_out": bytes_out,
            "frames": frames,
            "frames_timed_out": frames_timed_out,
            "max_frame_bytes": max_frame_bytes,
        },
    )


def exited(exit_code: int | None, signal_number: int | None) -> bytes:
    """`sidecar/exited`. Both fields are always present."""
    return _note(_EXITED, {"exit_code": exit_code, "signal": signal_number})


def initialize(tab_id: str, proof: str, pid: int) -> bytes:
    """The handshake request. `id` is 1; the sidecar sends no other request."""
    version: Json = {"major": _MAJOR, "minor": _MINOR}
    caps: list[Json] = []
    params: dict[str, Json] = {
        "protocol": [version, version],
        "role": _ROLE,
        "client_name": _NAME,
        "client_version": _VERSION,
        "pid": pid,
        "capabilities": caps,
        "locale": _LOCALE,
        "tab_id": tab_id,
        "sidecar_token": proof,
    }
    body: dict[str, Json] = {
        "kind": _REQUEST,
        "jsonrpc": _JSONRPC,
        "id": _REQUEST_ID,
        "method": _INITIALIZE,
        "params": params,
    }
    return _dump(body)


def _note(method: str, params: Params) -> bytes:
    body: dict[str, Json] = {"kind": _NOTIFICATION, "jsonrpc": _JSONRPC, "method": method, "params": params}
    return _dump(body)


def _dump(body: dict[str, Json]) -> bytes:
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    return encoded.encode() + b"\n"
