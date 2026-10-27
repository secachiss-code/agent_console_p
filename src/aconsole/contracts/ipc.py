"""NDJSON envelopes between the UI, the CLI, the sidecar, and the core."""

from typing import Annotated, Literal

import msgspec
from msgspec import Meta

from aconsole.contracts.common import AwareDatetime, ClientRole, TabId
from aconsole.contracts.errors import ErrorObject
from aconsole.contracts.events import UiEvent

JsonRpc = Literal["2.0"]
RequestId = Annotated[int, Meta(ge=1)]


class Request(msgspec.Struct, frozen=True, kw_only=True, tag="request", tag_field="kind"):
    jsonrpc: JsonRpc = "2.0"
    id: RequestId
    method: str
    params: msgspec.Raw
    timeout_ms: int | None = None


class Notification(msgspec.Struct, frozen=True, kw_only=True, tag="notification", tag_field="kind"):
    jsonrpc: JsonRpc = "2.0"
    method: str
    params: msgspec.Raw


class ResponseOk(msgspec.Struct, frozen=True, kw_only=True, tag="result", tag_field="kind"):
    jsonrpc: JsonRpc = "2.0"
    id: RequestId
    result: msgspec.Raw


class ResponseErr(msgspec.Struct, frozen=True, kw_only=True, tag="error", tag_field="kind"):
    jsonrpc: JsonRpc = "2.0"
    id: RequestId | None
    error: ErrorObject


Envelope = Request | Notification | ResponseOk | ResponseErr


class ProtocolVersion(msgspec.Struct, frozen=True, kw_only=True):
    major: Annotated[int, Meta(ge=1)]
    minor: Annotated[int, Meta(ge=0)]


class ProtocolLimits(msgspec.Struct, frozen=True, kw_only=True):
    max_message_bytes: int = 16 * 1024 * 1024
    event_ring_size: int = 4096
    max_inflight_requests: int = 64


class InitializeParams(msgspec.Struct, frozen=True, kw_only=True):
    protocol: tuple[ProtocolVersion, ProtocolVersion]
    role: ClientRole
    client_name: str
    client_version: str
    pid: int
    capabilities: frozenset[str]
    locale: str = "ru"
    tab_id: TabId | None = None
    sidecar_token: str | None = None


class InitializeResult(msgspec.Struct, frozen=True, kw_only=True):
    protocol: ProtocolVersion
    server_version: str
    server_pid: int
    session_id: str
    capabilities: frozenset[str]
    limits: ProtocolLimits


class CancelParams(msgspec.Struct, frozen=True, kw_only=True):
    id: RequestId


class EventEnvelope(msgspec.Struct, frozen=True, kw_only=True):
    seq: Annotated[int, Meta(ge=1)]
    at: AwareDatetime
    event: UiEvent


class SidecarStarted(msgspec.Struct, frozen=True, kw_only=True):
    child_pid: int
    started_at: AwareDatetime


class SidecarSignal(msgspec.Struct, frozen=True, kw_only=True):
    kind: Literal["bell", "osc_title", "osc9_notify", "osc777_notify", "osc9_4_progress"]
    text: str | None = None
    progress: Annotated[int, Meta(ge=0, le=100)] | None = None


class SidecarStats(msgspec.Struct, frozen=True, kw_only=True):
    bytes_out: int
    frames: int
    frames_timed_out: int
    max_frame_bytes: int


class SidecarExited(msgspec.Struct, frozen=True, kw_only=True):
    exit_code: int | None
    signal: int | None
