"""Stable presentation IDs carried by existing string wire fields.

CommandResult.toast, Notify.title/body and RestorePreviewResult.scope_note carry
these IDs. UI owns translation. External diagnostics and user-authored content
remain opaque text. Setup check titles carry setup.check.<check id>.
"""

from enum import StrEnum


class UiMessage(StrEnum):
    AGENT_NOT_STARTED = "review.agent_not_started"
    COMMENT_QUEUED = "review.comment_queued"
    CHECKPOINT_SCOPE = "checkpoint.scope.worktree"
    PALETTE_ITEM_UNAVAILABLE = "palette.item_unavailable"
    PALETTE_NOTE_UNAVAILABLE = "palette.note_unavailable"
    PALETTE_TERMINAL = "palette.current_terminal"
    PALETTE_APPROVALS = "palette.pending_requests"
    APPROVAL_REQUIRED = "notify.approval_required"
    CODEX_HOOKS_UNAVAILABLE = "notify.codex_hooks_unavailable"
    CODEX_HOOKS_HELP = "codex.hooks.help"
