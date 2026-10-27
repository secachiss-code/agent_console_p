"""Speak NDJSON to a core socket. `snapshot` and `events` are the A1.9 commands."""

import io
import os
import socket
import sys

import msgspec

from aconsole.contracts.app import AppSnapshot, SnapshotResult, SubscribeResult
from aconsole.contracts.common import ClientRole
from aconsole.contracts.ipc import (
    Envelope,
    InitializeParams,
    Notification,
    ProtocolVersion,
    Request,
    ResponseErr,
    ResponseOk,
)


def _request(request_id: int, method: str, params: msgspec.Struct | None = None) -> bytes:
    encoded = b"{}" if params is None else msgspec.json.encode(params)
    body = Request(id=request_id, method=method, params=msgspec.Raw(encoded))
    return msgspec.json.encode(body) + b"\n"


def _initialize() -> bytes:
    params = InitializeParams(
        protocol=(ProtocolVersion(major=1, minor=0), ProtocolVersion(major=1, minor=0)),
        role=ClientRole.CLI,
        client_name="devcli",
        client_version="0.0.0",
        pid=os.getpid(),
        capabilities=frozenset({"events.v1"}),
    )
    return _request(1, "initialize", params)


class _KindOnly(msgspec.Struct, frozen=True, kw_only=True):
    kind: str


def _read_envelope(source: io.BufferedIOBase) -> Envelope:
    line = source.readline()
    if line == b"":
        message = "core closed the socket"
        raise ConnectionError(message)
    return _decode_line(line)


def _decode_line(line: bytes) -> Envelope:
    kind = msgspec.json.decode(line, type=_KindOnly).kind
    if kind == "request":
        return msgspec.json.decode(line, type=Request)
    if kind == "notification":
        return msgspec.json.decode(line, type=Notification)
    if kind == "result":
        return msgspec.json.decode(line, type=ResponseOk)
    if kind == "error":
        return msgspec.json.decode(line, type=ResponseErr)
    message = "core sent an unknown envelope"
    raise ConnectionError(message)


def request_frame(path: str, method: str, params: msgspec.Struct | None = None) -> Envelope:
    frames = _exchange(path, (_initialize(), _request(2, method, params)), 0)
    return frames[-1]


def fetch_snapshot(path: str) -> AppSnapshot:
    frames = _exchange(path, (_initialize(), _request(2, "app/snapshot")), 0)
    response = frames[-1]
    if not isinstance(response, ResponseOk):
        message = "snapshot failed"
        raise ConnectionError(message)
    decoded = msgspec.json.decode(response.result, type=SnapshotResult)
    return decoded.snapshot


def collect_event_types(path: str) -> frozenset[str]:
    subscribe = _request(3, "events/subscribe", _After(after_seq=0))
    with _socket(path) as sock:
        sock.sendall(_initialize())
        sock.sendall(subscribe)
        reader = sock.makefile("rb")
        _ = _read_envelope(reader)
        subscribed_frame = _read_envelope(reader)
        if not isinstance(subscribed_frame, ResponseOk):
            message = "subscribe failed"
            raise ConnectionError(message)
        subscribed = msgspec.json.decode(subscribed_frame.result, type=SubscribeResult)
        names: set[str] = set()
        for _ in range(subscribed.current_seq):
            frame = _read_envelope(reader)
            if isinstance(frame, Notification):
                body = msgspec.json.decode(frame.params, type=_EventFrame)
                names.add(body.event.kind)
        return frozenset(names)


def _exchange(path: str, payloads: tuple[bytes, ...], extra: int) -> tuple[Envelope, ...]:
    with _socket(path) as sock:
        for payload in payloads:
            sock.sendall(payload)
        reader = sock.makefile("rb")
        return tuple(_read_envelope(reader) for _ in range(len(payloads) + extra))


def _socket(path: str) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(3)
    sock.connect(path)
    return sock


class _After(msgspec.Struct, frozen=True, kw_only=True):
    after_seq: int


class _Kind(msgspec.Struct, frozen=True, kw_only=True):
    kind: str = msgspec.field(name="type")


class _EventFrame(msgspec.Struct, frozen=True, kw_only=True):
    event: _Kind


_ARGV = 3


def main(argv: list[str]) -> int:
    if len(argv) != _ARGV or argv[0] not in {"snapshot", "events"} or argv[1] != "--socket":
        _ = sys.stdout.write("usage: devcli snapshot|events --socket PATH\n")
        return 2
    if argv[0] == "snapshot":
        snapshot = fetch_snapshot(argv[2])
        _ = sys.stdout.write(f"projects {len(snapshot.projects)} tabs {len(snapshot.tabs)}\n")
        return 0
    for name in sorted(collect_event_types(argv[2])):
        _ = sys.stdout.write(name + "\n")
    return 0
