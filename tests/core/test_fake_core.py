"""Fake core over a real unix socket, read back by devcli."""

from __future__ import annotations

import os
import socket
import tempfile
from typing import TYPE_CHECKING

import msgspec
import pytest

from aconsole.contracts.app import (
    CheckpointListResult,
    ChecksResult,
    DiffSummaryResult,
    PaletteItemsResult,
    SubscribeParams,
    SubscribeResult,
    TerminalLaunchSpec,
)
from aconsole.contracts.common import ClientRole, QuotaLevel, TabStatus
from aconsole.contracts.errors import ERRORS, error_by_name
from aconsole.contracts.ipc import (
    EventEnvelope,
    InitializeParams,
    Notification,
    ProtocolVersion,
    Request,
    ResponseErr,
    ResponseOk,
)

if TYPE_CHECKING:
    import io

    from aconsole.contracts.ipc import Envelope

from aconsole.contracts.methods import METHODS
from aconsole.core.__main__ import main as core_main
from aconsole.core.__main__ import socket_from
from aconsole.core.infrastructure.fake.results import covers_methods, result_type
from aconsole.core.infrastructure.fake.scenarios import APP_EVENT_TYPES
from aconsole.core.infrastructure.fake.server import FakeCore, load_script
from aconsole.devcli.client import collect_event_types, fetch_snapshot, main, request_frame


def _core(scenario: str) -> tuple[FakeCore, str]:
    directory = tempfile.mkdtemp(prefix="aconsole-fake-")
    path = f"{directory}/core.sock"
    core = FakeCore(path, scenario)
    core.start()
    return core, path


def test_fake_demo_events_match_app_api() -> None:
    core, path = _core("demo")
    try:
        snapshot = fetch_snapshot(path)
        assert len(snapshot.projects) == 2
        assert len(snapshot.tabs) == 5
        assert len(snapshot.accounts) == 2
        assert len(snapshot.approvals) == 2
        assert snapshot.plans[0].total == 8
        assert collect_event_types(path) == APP_EVENT_TYPES
        assert main(["snapshot", "--socket", path]) == 0
        assert main(["events", "--socket", path]) == 0
        palette = request_frame(path, "palette/items")
        diff = request_frame(path, "diff/summary")
        points = request_frame(path, "checkpoint/list")
        launch = request_frame(path, "tab/launchSpec")
        assert isinstance(palette, ResponseOk)
        assert isinstance(diff, ResponseOk)
        assert isinstance(points, ResponseOk)
        assert isinstance(launch, ResponseOk)
        assert len(msgspec.json.decode(palette.result, type=PaletteItemsResult).items) == 40
        assert len(msgspec.json.decode(diff.result, type=DiffSummaryResult).files) == 3
        assert len(msgspec.json.decode(points.result, type=CheckpointListResult).checkpoints) == 6
        assert msgspec.json.decode(launch.result, type=TerminalLaunchSpec).argv[0] == "/bin/sh"
    finally:
        core.stop()


def test_fake_empty_is_the_first_run() -> None:
    core, path = _core("empty")
    try:
        snapshot = fetch_snapshot(path)
        assert snapshot.tabs == ()
        assert snapshot.setup.required is True
        checks = request_frame(path, "setup/checks")
        assert isinstance(checks, ResponseOk)
        assert len(msgspec.json.decode(checks.result, type=ChecksResult).checks) == 2
    finally:
        core.stop()


def test_fake_stress_has_fifty_tabs() -> None:
    core, path = _core("stress")
    try:
        assert len(fetch_snapshot(path).tabs) == 50
    finally:
        core.stop()


def test_fake_errors_use_the_registry() -> None:
    core, path = _core("errors")
    try:
        frame = request_frame(path, "app/snapshot")
        assert isinstance(frame, ResponseErr)
        assert frame.error.code == ERRORS[0].code
    finally:
        core.stop()


def test_fake_usage_and_unknown_scenario() -> None:
    assert main([]) == 2
    assert core_main([]) == 2
    parsed = socket_from(["--fake", "demo"])
    assert parsed is not None
    assert parsed[0] == "demo"
    assert parsed[1].endswith("core.sock")
    with pytest.raises(ValueError, match="unknown fake scenario"):
        load_script("nope")


def test_fake_methods_return_the_result_type() -> None:
    assert covers_methods()
    core, path = _core("demo")
    try:
        for method in METHODS:
            frame = request_frame(path, method.name)
            assert isinstance(frame, ResponseOk), method.name
            kind = result_type(method.name)
            assert kind is not None
            decoded = msgspec.json.decode(frame.result, type=kind)
            assert isinstance(decoded, kind)
        missing = request_frame(path, "no/such")
        assert isinstance(missing, ResponseErr)
        assert missing.error.code == error_by_name("METHOD_NOT_FOUND").code
    finally:
        core.stop()


def test_fake_waiting_and_quota_scenarios() -> None:
    waiting, waiting_path = _core("waiting")
    quota, quota_path = _core("quota-critical")
    try:
        tabs = fetch_snapshot(waiting_path).tabs
        assert {tab.status for tab in tabs} == {
            TabStatus.WAITING_INPUT,
            TabStatus.UNKNOWN,
            TabStatus.EXITED,
        }
        levels = {window.level for account in fetch_snapshot(quota_path).accounts for window in account.quota}
        assert QuotaLevel.CRITICAL in levels
        assert QuotaLevel.UNKNOWN in levels
    finally:
        waiting.stop()
        quota.stop()


def test_fake_approval_expired_is_deterministic() -> None:
    core, path = _core("approval-expired")
    try:
        frame = request_frame(path, "approval/decide")
        assert isinstance(frame, ResponseErr)
        assert frame.error.code == error_by_name("APPROVAL_EXPIRED").code
    finally:
        core.stop()


def test_fake_second_subscribe_does_not_replay_scripted_events() -> None:
    core, path = _core("demo")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(3)
            sock.connect(path)
            reader = sock.makefile("rb")
            try:
                _send(sock, _initialize_frame())
                initialized = _read_frame(reader)
                assert isinstance(initialized, ResponseOk)
                _send(sock, _subscribe_frame(2, 0))
                first = _read_frame(reader)
                assert isinstance(first, ResponseOk)
                subscribed = msgspec.json.decode(first.result, type=SubscribeResult)
                seqs = tuple(_event_seq(reader) for _ in range(subscribed.current_seq))
                assert seqs == tuple(range(1, subscribed.current_seq + 1))
                _send(sock, _subscribe_frame(3, subscribed.current_seq))
                second = _read_frame(reader)
                assert isinstance(second, ResponseOk)
                again = msgspec.json.decode(second.result, type=SubscribeResult)
                assert again.current_seq == subscribed.current_seq
                sock.settimeout(0.2)
                try:
                    extra = _read_frame(reader)
                except TimeoutError:
                    extra = None
                assert extra is None
            finally:
                reader.close()
    finally:
        core.stop()


class _KindOnly(msgspec.Struct, frozen=True, kw_only=True):
    kind: str


def _initialize_frame() -> bytes:
    params = InitializeParams(
        protocol=(ProtocolVersion(major=1, minor=0), ProtocolVersion(major=1, minor=0)),
        role=ClientRole.CLI,
        client_name="fake-replay-test",
        client_version="0.0.0",
        pid=os.getpid(),
        capabilities=frozenset({"events.v1"}),
    )
    return _frame(1, "initialize", params)


def _subscribe_frame(request_id: int, after_seq: int) -> bytes:
    return _frame(request_id, "events/subscribe", SubscribeParams(after_seq=after_seq))


def _frame(request_id: int, method: str, params: msgspec.Struct) -> bytes:
    body = Request(id=request_id, method=method, params=msgspec.Raw(msgspec.json.encode(params)))
    return msgspec.json.encode(body) + b"\n"


def _send(sock: socket.socket, payload: bytes) -> None:
    sock.sendall(payload)


def _read_frame(reader: io.BufferedIOBase) -> Envelope:
    line = reader.readline()
    if line == b"":
        message = "core closed the socket"
        raise ConnectionError(message)
    kind = msgspec.json.decode(line, type=_KindOnly).kind
    if kind == "result":
        return msgspec.json.decode(line, type=ResponseOk)
    if kind == "error":
        return msgspec.json.decode(line, type=ResponseErr)
    if kind == "notification":
        return msgspec.json.decode(line, type=Notification)
    message = "unexpected envelope"
    raise ConnectionError(message)


def _event_seq(reader: io.BufferedIOBase) -> int:
    frame = _read_frame(reader)
    assert isinstance(frame, Notification)
    assert frame.method == "$/event"
    return msgspec.json.decode(frame.params, type=EventEnvelope).seq
