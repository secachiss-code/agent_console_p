"""Scalars and enumerations shared by every contract module."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, NewType

from msgspec import Meta

AwareDatetime = Annotated[datetime, Meta(tz=True)]
Ulid = Annotated[str, Meta(pattern="^[0-9A-HJKMNP-TV-Z]{26}$")]
Percent = Annotated[float, Meta(ge=0, le=100)]

ProjectId = NewType("ProjectId", str)
TabId = NewType("TabId", str)
PlanId = NewType("PlanId", str)
NodeId = NewType("NodeId", str)
ApprovalId = NewType("ApprovalId", str)
CheckpointId = NewType("CheckpointId", str)
AccountId = NewType("AccountId", str)
NoteRef = NewType("NoteRef", str)


class AgentKind(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"
    OPENCODE = "opencode"
    GROK = "grok"
    CURSOR = "cursor"


class TabStatus(StrEnum):
    STARTING = "starting"
    IDLE = "idle"
    WORKING = "working"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_INPUT = "waiting_input"
    ERROR = "error"
    EXITED = "exited"
    UNKNOWN = "unknown"


class QuotaLevel(StrEnum):
    OK = "ok"
    WARN = "warn"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class NodeStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VERIFYING = "verifying"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EdgeKind(StrEnum):
    BLOCKS = "blocks"
    PARENT = "parent"
    RELATED = "related"


class WorkspaceMode(StrEnum):
    NEW_WORKTREE = "new_worktree"
    MAIN_CHECKOUT = "main_checkout"
    EXISTING_WORKTREE = "existing_worktree"


class ApprovalDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class ApprovalScope(StrEnum):
    ONCE = "once"
    TAB_SESSION = "tab_session"


class RiskTag(StrEnum):
    DESTRUCTIVE = "destructive"
    OUTSIDE_WORKTREE = "outside_worktree"
    NETWORK = "network"
    PUBLISH = "publish"
    SECRETS = "secrets"
    PRIVILEGE = "privilege"


class ClientRole(StrEnum):
    UI = "ui"
    SIDECAR = "sidecar"
    CLI = "cli"


class PaletteKind(StrEnum):
    COMMAND = "command"
    NATIVE_SKILL = "native_skill"
    PLAN_NODE = "plan_node"
    NOTE = "note"
    TAB = "tab"
    SESSION = "session"
    ACTION = "action"
    APPROVAL = "approval"
