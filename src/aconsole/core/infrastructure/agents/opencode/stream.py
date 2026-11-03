"""OpenCode's server-sent events become the hook events the tab book already knows.

One thread per tab reads `/api/event`. It uses `http.client` because the stream may be chunked. The thread ends by
itself once the server has been gone for a while, which is what happens when the agent exits.
"""

from __future__ import annotations

import base64
import contextlib
import http.client
import logging
import socket
import threading
from collections.abc import Callable
from typing import Final, final

import msgspec

_LOG = logging.getLogger(__name__)
_HOST: Final = "127.0.0.1"
_USER: Final = "opencode"
_OK: Final = 200
_LINE_BYTES: Final = 1024 * 1024
_CONNECT_S: Final = 2.0
_RETRY_S: Final = 1.0
# The terminal starts after the launch spec is returned, and the server after that.
_FIRST_TRIES: Final = 120
_LATER_TRIES: Final = 10
_WORKING: Final = "UserPromptSubmit"
_ASKED: Final = "PermissionRequest"
_ANSWERED: Final = "PreToolUse"
_IDLE: Final = "agent-turn-complete"
_FAILED: Final = "StopFailure"
_USAGE: Final = "session.usage.updated"
_STARTED: Final = "session.execution.started"
_FINISHED: Final = frozenset({"session.execution.succeeded", "session.execution.interrupted"})
_BROKE: Final = "session.execution.failed"
_WANTED: Final = frozenset(
    {
        "session.created",
        _STARTED,
        *_FINISHED,
        _BROKE,
        _USAGE,
        "permission.asked",
        "permission.replied",
    },
)
# Not a status: the tab's running total of what its model calls cost, in dollars, priced by OpenCode itself.
COST_EVENT: Final = "cost"
_COST_STEP: Final = 0.0005


class _Head(msgspec.Struct, frozen=True):
    type: str = ""


class _Data(msgspec.Struct, frozen=True):
    sessionID: str | None = None  # noqa: N815 -- OpenCode's own name
    parentID: str | None = None  # noqa: N815 -- OpenCode's own name
    requestID: str | None = None  # noqa: N815 -- OpenCode's own name
    id: str | None = None
    cost: float | None = None


class _Event(msgspec.Struct, frozen=True):
    type: str = ""
    data: _Data = _Data()


class _Signal(msgspec.Struct, frozen=True, omit_defaults=True):
    """The ingest body: named as a hook's, so the consumer reads it the same way."""

    hook_event_name: str
    session_id: str | None = None
    cost_usd: float | None = None


@final
class EventReader:
    """Turn one tab's events into ingest bodies. Only a change of meaning produces one.

    A subagent runs in a child session: its idle must not end the tab's turn, but its permission request still needs
    the user. A turn that starts while a request is open must not hide the wait.
    """

    def __init__(self) -> None:
        self._children: set[str] = set()
        self._open: set[str] = set()
        self._last: str | None = None
        # Each session's running total, latest seen.
        self._costs: dict[str, float] = {}
        self._said_cost: float = 0.0

    def read(self, data: bytes) -> bytes | None:
        """The ingest body for one event's JSON, or None when nothing changes."""
        try:
            if msgspec.json.decode(data, type=_Head).type not in _WANTED:
                return None
            event = msgspec.json.decode(data, type=_Event)
        except msgspec.DecodeError:
            return None
        if event.type == _USAGE:
            return self._cost(event.data)
        name = self._meaning(event)
        if name is None or name == self._last:
            return None
        self._last = name
        root = event.data.sessionID if name == _WORKING else None
        return msgspec.json.encode(_Signal(hook_event_name=name, session_id=root))

    def _cost(self, data: _Data) -> bytes | None:
        """The tab's total, subagents included: they spend on its behalf. Said again only when it moved."""
        if data.sessionID is None or data.cost is None or data.cost < 0:
            return None
        self._costs[data.sessionID] = data.cost
        total = round(sum(self._costs.values()), 4)
        if abs(total - self._said_cost) < _COST_STEP:
            return None
        self._said_cost = total
        return msgspec.json.encode(_Signal(hook_event_name=COST_EVENT, cost_usd=total))

    def _meaning(self, event: _Event) -> str | None:
        data = event.data
        kind = event.type
        if kind == "session.created":
            if data.parentID and data.sessionID:
                self._children.add(data.sessionID)
            return None
        if kind == "permission.asked":
            self._open.add(data.id or "")
            return _ASKED
        if kind == "permission.replied":
            self._open.discard(data.requestID or "")
            return None if self._open else _ANSWERED
        return None if data.sessionID in self._children else self._root_meaning(kind)

    def _root_meaning(self, kind: str) -> str | None:
        if kind == _BROKE:
            self._open.clear()
            return _FAILED
        if kind in _FINISHED:
            self._open.clear()
            return _IDLE
        return _WORKING if kind == _STARTED and not self._open else None


def data_lines(lines: Callable[[], bytes]) -> list[bytes]:
    """The `data:` of one server-sent event. Empty at the end of the stream."""
    parts: list[bytes] = []
    while line := lines():
        text = line.rstrip(b"\r\n")
        if text == b"" and parts:
            return [b"\n".join(parts)]
        if text.startswith(b"data:"):
            parts.append(text[5:].removeprefix(b" "))
    return []


@final
class EventStream:
    """Read one server's events until it is gone or `stop` is called."""

    def __init__(self, port: int, token: str, heard: Callable[[bytes], None]) -> None:
        self._port = port
        self._auth = base64.b64encode(f"{_USER}:{token}".encode()).decode()
        self._heard = heard
        self._reader = EventReader()
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._socket: socket.socket | None = None
        self._thread = threading.Thread(target=self._run, name=f"opencode-events-{port}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        with self._lock:
            live = self._socket
        if live is not None:
            with contextlib.suppress(OSError):
                live.shutdown(socket.SHUT_RDWR)

    def _run(self) -> None:
        tries = _FIRST_TRIES
        while tries > 0 and not self._stopped.is_set():
            try:
                if self._read_once():
                    tries = _LATER_TRIES
            except (OSError, http.client.HTTPException) as exc:
                _LOG.debug("OpenCode events on port %d: %s", self._port, exc)
            tries -= 1
            _ = self._stopped.wait(_RETRY_S)

    def _read_once(self) -> bool:
        """True when the server answered, even if it then went away."""
        connection = http.client.HTTPConnection(_HOST, self._port, timeout=_CONNECT_S)
        try:
            connection.connect()
            live = connection.sock
            if not isinstance(live, socket.socket):
                return False
            with self._lock:
                self._socket = live
            # A quiet stream is normal, and `stop` wakes the read.
            live.settimeout(None)
            headers = {"Accept": "text/event-stream", "Authorization": f"Basic {self._auth}"}
            connection.request("GET", "/api/event", headers=headers)
            response = connection.getresponse()
            if response.status != _OK:
                _LOG.warning("OpenCode events on port %d: HTTP %d", self._port, response.status)
                return False
            self._pump(lambda: response.readline(_LINE_BYTES))
            return True
        finally:
            with self._lock:
                self._socket = None
            connection.close()

    def _pump(self, lines: Callable[[], bytes]) -> None:
        while not self._stopped.is_set():
            found = data_lines(lines)
            if not found:
                return
            body = self._reader.read(found[0])
            if body is not None:
                self._heard(body)
