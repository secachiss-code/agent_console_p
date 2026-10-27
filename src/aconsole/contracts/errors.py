"""Error registry and the AIP-193 payload carried on every transport.

Detail variants are discriminated by `type` (the registry name). `ErrorData.reason`
carries the same name. `type` is the discriminator because `VAULT_EDIT_REJECTED`
already uses `reason` as a payload field.
"""

from typing import Literal

import msgspec

ErrorDomain = Literal["aconsole.ipc", "aconsole.ingest", "aconsole.core", "aconsole.mcp"]


class ErrorSpec(msgspec.Struct, frozen=True, kw_only=True):
    code: int
    name: str
    retryable: bool


class MethodNotFoundDetails(msgspec.Struct, frozen=True, kw_only=True, tag="METHOD_NOT_FOUND", tag_field="type"):
    method: str


class InvalidParamsDetails(msgspec.Struct, frozen=True, kw_only=True, tag="INVALID_PARAMS", tag_field="type"):
    path: str
    expected: str


class InternalErrorDetails(msgspec.Struct, frozen=True, kw_only=True, tag="INTERNAL_ERROR", tag_field="type"):
    incident_id: str


class ProtocolVersionUnsupportedDetails(
    msgspec.Struct, frozen=True, kw_only=True, tag="PROTOCOL_VERSION_UNSUPPORTED", tag_field="type"
):
    supported: tuple[str, ...]


class MessageTooLargeDetails(msgspec.Struct, frozen=True, kw_only=True, tag="MESSAGE_TOO_LARGE", tag_field="type"):
    limit: int


class ServerBusyDetails(msgspec.Struct, frozen=True, kw_only=True, tag="SERVER_BUSY", tag_field="type"):
    retry_after_ms: int


class ResyncRequiredDetails(msgspec.Struct, frozen=True, kw_only=True, tag="RESYNC_REQUIRED", tag_field="type"):
    oldest_seq: int


class RoleForbiddenDetails(msgspec.Struct, frozen=True, kw_only=True, tag="ROLE_FORBIDDEN", tag_field="type"):
    role: str


class IngestPayloadInvalidDetails(
    msgspec.Struct, frozen=True, kw_only=True, tag="INGEST_PAYLOAD_INVALID", tag_field="type"
):
    agent: str
    event: str


class ProjectNotFoundDetails(msgspec.Struct, frozen=True, kw_only=True, tag="PROJECT_NOT_FOUND", tag_field="type"):
    project_id: str


class ProjectNotGitRepoDetails(
    msgspec.Struct, frozen=True, kw_only=True, tag="PROJECT_NOT_A_GIT_REPO", tag_field="type"
):
    path: str


class TabNotFoundDetails(msgspec.Struct, frozen=True, kw_only=True, tag="TAB_NOT_FOUND", tag_field="type"):
    tab_id: str


class TabStateConflictDetails(msgspec.Struct, frozen=True, kw_only=True, tag="TAB_STATE_CONFLICT", tag_field="type"):
    status: str


class AgentUnavailableDetails(msgspec.Struct, frozen=True, kw_only=True, tag="AGENT_UNAVAILABLE", tag_field="type"):
    agent: str
    container: str


class AgentNotLoggedInDetails(msgspec.Struct, frozen=True, kw_only=True, tag="AGENT_NOT_LOGGED_IN", tag_field="type"):
    agent: str


class ContainerStartFailedDetails(
    msgspec.Struct, frozen=True, kw_only=True, tag="CONTAINER_START_FAILED", tag_field="type"
):
    container: str
    stderr_tail: str


class WorktreeCreateFailedDetails(
    msgspec.Struct, frozen=True, kw_only=True, tag="WORKTREE_CREATE_FAILED", tag_field="type"
):
    stderr_tail: str


class WorktreeDirtyDetails(msgspec.Struct, frozen=True, kw_only=True, tag="WORKTREE_DIRTY", tag_field="type"):
    dirty_summary: str


class BranchCheckedOutDetails(
    msgspec.Struct, frozen=True, kw_only=True, tag="BRANCH_ALREADY_CHECKED_OUT", tag_field="type"
):
    branch: str
    worktree: str


class PathNotAllowedDetails(msgspec.Struct, frozen=True, kw_only=True, tag="PATH_NOT_ALLOWED", tag_field="type"):
    path: str


class SessionNotFoundDetails(msgspec.Struct, frozen=True, kw_only=True, tag="SESSION_NOT_FOUND", tag_field="type"):
    session_id: str


class ApprovalNotFoundDetails(msgspec.Struct, frozen=True, kw_only=True, tag="APPROVAL_NOT_FOUND", tag_field="type"):
    request_id: str


class ApprovalExpiredDetails(msgspec.Struct, frozen=True, kw_only=True, tag="APPROVAL_EXPIRED", tag_field="type"):
    request_id: str


class ApprovalAlreadyDecidedDetails(
    msgspec.Struct, frozen=True, kw_only=True, tag="APPROVAL_ALREADY_DECIDED", tag_field="type"
):
    decision: str


class CheckpointNotFoundDetails(
    msgspec.Struct, frozen=True, kw_only=True, tag="CHECKPOINT_NOT_FOUND", tag_field="type"
):
    checkpoint_id: str


class CheckpointRestoreConflictDetails(
    msgspec.Struct, frozen=True, kw_only=True, tag="CHECKPOINT_RESTORE_CONFLICT", tag_field="type"
):
    status: str


class DiffTooLargeDetails(msgspec.Struct, frozen=True, kw_only=True, tag="DIFF_TOO_LARGE", tag_field="type"):
    files: int
    byte_count: int = msgspec.field(name="bytes")


class PlanNotFoundDetails(msgspec.Struct, frozen=True, kw_only=True, tag="PLAN_NOT_FOUND", tag_field="type"):
    plan_id: str


class NodeNotFoundDetails(msgspec.Struct, frozen=True, kw_only=True, tag="NODE_NOT_FOUND", tag_field="type"):
    node_id: str


class PlanCycleDetails(msgspec.Struct, frozen=True, kw_only=True, tag="PLAN_CYCLE", tag_field="type"):
    cycle: tuple[str, ...]


class NodeTransitionInvalidDetails(
    msgspec.Struct, frozen=True, kw_only=True, tag="NODE_TRANSITION_INVALID", tag_field="type"
):
    from_status: str = msgspec.field(name="from")
    to: str
    allowed: tuple[str, ...]


class NodeNotReadyDetails(msgspec.Struct, frozen=True, kw_only=True, tag="NODE_NOT_READY", tag_field="type"):
    blocked_by: tuple[str, ...]


class LeaseHeldDetails(msgspec.Struct, frozen=True, kw_only=True, tag="LEASE_HELD", tag_field="type"):
    holder_tab: str
    expires_at: str


class LeaseStaleFenceDetails(msgspec.Struct, frozen=True, kw_only=True, tag="LEASE_STALE_FENCE", tag_field="type"):
    current_fence: int


class PathsOverlapDetails(msgspec.Struct, frozen=True, kw_only=True, tag="PATHS_OVERLAP", tag_field="type"):
    conflicting_node: str


class VaultWriteDeferredDetails(
    msgspec.Struct, frozen=True, kw_only=True, tag="VAULT_WRITE_DEFERRED", tag_field="type"
):
    note_ref: str


class VaultConflictDetails(msgspec.Struct, frozen=True, kw_only=True, tag="VAULT_CONFLICT", tag_field="type"):
    note_ref: str
    fields: tuple[str, ...]


class VaultEditRejectedDetails(msgspec.Struct, frozen=True, kw_only=True, tag="VAULT_EDIT_REJECTED", tag_field="type"):
    note_ref: str
    reason: str


class NoteNotFoundDetails(msgspec.Struct, frozen=True, kw_only=True, tag="NOTE_NOT_FOUND", tag_field="type"):
    note_ref: str


class CodegraphExtractorFailedDetails(
    msgspec.Struct, frozen=True, kw_only=True, tag="CODEGRAPH_EXTRACTOR_FAILED", tag_field="type"
):
    language: str
    stderr_tail: str


class ImpactTargetUnknownDetails(
    msgspec.Struct, frozen=True, kw_only=True, tag="IMPACT_TARGET_UNKNOWN", tag_field="type"
):
    target: str


class SetupFixUnknownDetails(msgspec.Struct, frozen=True, kw_only=True, tag="SETUP_FIX_UNKNOWN", tag_field="type"):
    fix_id: str


class SetupFixFailedDetails(msgspec.Struct, frozen=True, kw_only=True, tag="SETUP_FIX_FAILED", tag_field="type"):
    step: str
    stderr_tail: str


class AgentConfigUnsupportedDetails(
    msgspec.Struct, frozen=True, kw_only=True, tag="AGENT_CONFIG_UNSUPPORTED_VERSION", tag_field="type"
):
    agent: str
    version: str
    supported: str


class UnexpectedDetails(msgspec.Struct, frozen=True, kw_only=True, tag="UNEXPECTED", tag_field="type"):
    incident_id: str


ErrorDetails = (
    MethodNotFoundDetails
    | InvalidParamsDetails
    | InternalErrorDetails
    | ProtocolVersionUnsupportedDetails
    | MessageTooLargeDetails
    | ServerBusyDetails
    | ResyncRequiredDetails
    | RoleForbiddenDetails
    | IngestPayloadInvalidDetails
    | ProjectNotFoundDetails
    | ProjectNotGitRepoDetails
    | TabNotFoundDetails
    | TabStateConflictDetails
    | AgentUnavailableDetails
    | AgentNotLoggedInDetails
    | ContainerStartFailedDetails
    | WorktreeCreateFailedDetails
    | WorktreeDirtyDetails
    | BranchCheckedOutDetails
    | PathNotAllowedDetails
    | SessionNotFoundDetails
    | ApprovalNotFoundDetails
    | ApprovalExpiredDetails
    | ApprovalAlreadyDecidedDetails
    | CheckpointNotFoundDetails
    | CheckpointRestoreConflictDetails
    | DiffTooLargeDetails
    | PlanNotFoundDetails
    | NodeNotFoundDetails
    | PlanCycleDetails
    | NodeTransitionInvalidDetails
    | NodeNotReadyDetails
    | LeaseHeldDetails
    | LeaseStaleFenceDetails
    | PathsOverlapDetails
    | VaultWriteDeferredDetails
    | VaultConflictDetails
    | VaultEditRejectedDetails
    | NoteNotFoundDetails
    | CodegraphExtractorFailedDetails
    | ImpactTargetUnknownDetails
    | SetupFixUnknownDetails
    | SetupFixFailedDetails
    | AgentConfigUnsupportedDetails
    | UnexpectedDetails
)


class ErrorData(msgspec.Struct, frozen=True, kw_only=True):
    reason: str
    domain: ErrorDomain
    retryable: bool
    user_message_key: str
    user_message_args: dict[str, str] = msgspec.field(default_factory=dict)
    hint: str | None = None
    details: ErrorDetails | None = None


class ErrorObject(msgspec.Struct, frozen=True, kw_only=True):
    code: int
    message: str
    data: ErrorData | None = None


def _spec(code: int, name: str, *, retryable: bool) -> ErrorSpec:
    return ErrorSpec(code=code, name=name, retryable=retryable)


ERRORS: tuple[ErrorSpec, ...] = (
    _spec(-32700, "PARSE_ERROR", retryable=False),
    _spec(-32600, "INVALID_REQUEST", retryable=False),
    _spec(-32601, "METHOD_NOT_FOUND", retryable=False),
    _spec(-32602, "INVALID_PARAMS", retryable=False),
    _spec(-32603, "INTERNAL_ERROR", retryable=False),
    _spec(-32001, "NOT_INITIALIZED", retryable=False),
    _spec(-32002, "ALREADY_INITIALIZED", retryable=False),
    _spec(-32003, "PROTOCOL_VERSION_UNSUPPORTED", retryable=False),
    _spec(-32004, "MESSAGE_TOO_LARGE", retryable=False),
    _spec(-32005, "SERVER_BUSY", retryable=True),
    _spec(-32006, "RESYNC_REQUIRED", retryable=True),
    _spec(-32007, "SIDECAR_TOKEN_INVALID", retryable=False),
    _spec(-32008, "ROLE_FORBIDDEN", retryable=False),
    _spec(-32800, "REQUEST_CANCELLED", retryable=False),
    _spec(-32801, "CONTENT_MODIFIED", retryable=True),
    _spec(-32802, "SERVER_CANCELLED", retryable=True),
    _spec(1001, "INGEST_UNAUTHORIZED", retryable=False),
    _spec(1002, "INGEST_TAB_MISMATCH", retryable=False),
    _spec(1003, "INGEST_PAYLOAD_INVALID", retryable=False),
    _spec(1004, "INGEST_ORIGIN_FORBIDDEN", retryable=False),
    _spec(2001, "PROJECT_NOT_FOUND", retryable=False),
    _spec(2002, "PROJECT_NOT_A_GIT_REPO", retryable=False),
    _spec(2003, "TAB_NOT_FOUND", retryable=False),
    _spec(2004, "TAB_STATE_CONFLICT", retryable=False),
    _spec(2005, "AGENT_UNAVAILABLE", retryable=True),
    _spec(2006, "AGENT_NOT_LOGGED_IN", retryable=False),
    _spec(2007, "CONTAINER_START_FAILED", retryable=True),
    _spec(2008, "WORKTREE_CREATE_FAILED", retryable=False),
    _spec(2009, "WORKTREE_DIRTY", retryable=False),
    _spec(2010, "BRANCH_ALREADY_CHECKED_OUT", retryable=False),
    _spec(2011, "PATH_NOT_ALLOWED", retryable=False),
    _spec(2012, "SESSION_NOT_FOUND", retryable=False),
    _spec(3001, "APPROVAL_NOT_FOUND", retryable=False),
    _spec(3002, "APPROVAL_EXPIRED", retryable=False),
    _spec(3003, "APPROVAL_NONCE_MISMATCH", retryable=False),
    _spec(3004, "APPROVAL_ALREADY_DECIDED", retryable=False),
    _spec(3005, "CHECKPOINT_NOT_FOUND", retryable=False),
    _spec(3006, "CHECKPOINT_RESTORE_CONFLICT", retryable=False),
    _spec(3007, "CONFIRM_TOKEN_INVALID", retryable=False),
    _spec(3008, "DIFF_TOO_LARGE", retryable=False),
    _spec(4001, "PLAN_NOT_FOUND", retryable=False),
    _spec(4002, "NODE_NOT_FOUND", retryable=False),
    _spec(4003, "PLAN_CYCLE", retryable=False),
    _spec(4004, "NODE_TRANSITION_INVALID", retryable=False),
    _spec(4005, "NODE_NOT_READY", retryable=False),
    _spec(4006, "LEASE_HELD", retryable=True),
    _spec(4007, "LEASE_STALE_FENCE", retryable=False),
    _spec(4008, "LEASE_EXPIRED", retryable=False),
    _spec(4009, "NODE_EVIDENCE_REQUIRED", retryable=False),
    _spec(4010, "PATHS_OVERLAP", retryable=True),
    _spec(5001, "VAULT_NOT_CONFIGURED", retryable=False),
    _spec(5002, "VAULT_WRITE_DEFERRED", retryable=True),
    _spec(5003, "VAULT_CONFLICT", retryable=False),
    _spec(5004, "VAULT_EDIT_REJECTED", retryable=False),
    _spec(5005, "NOTE_NOT_FOUND", retryable=False),
    _spec(5006, "CODEGRAPH_EXTRACTOR_FAILED", retryable=True),
    _spec(5007, "IMPACT_TARGET_UNKNOWN", retryable=False),
    _spec(6001, "SETUP_FIX_UNKNOWN", retryable=False),
    _spec(6002, "SETUP_FIX_FAILED", retryable=True),
    _spec(6003, "AGENT_CONFIG_UNSUPPORTED_VERSION", retryable=False),
    _spec(9001, "UNEXPECTED", retryable=False),
)


def error_by_name(name: str) -> ErrorSpec:
    for spec in ERRORS:
        if spec.name == name:
            return spec
    message = f"unknown error name {name}"
    raise KeyError(message)
