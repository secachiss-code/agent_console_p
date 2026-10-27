"""Method table. UI and core signatures are generated from this, not written twice."""

import msgspec

UI_CLI = ("ui", "cli")
ALL_ROLES = ("ui", "cli", "sidecar")


class Method(msgspec.Struct, frozen=True, kw_only=True):
    name: str
    params: str
    result: str
    errors: tuple[str, ...] = ()
    roles: tuple[str, ...] = UI_CLI
    long: bool = False
    capability: str | None = None


METHODS: tuple[Method, ...] = (
    Method(
        name="initialize",
        params="InitializeParams",
        result="InitializeResult",
        errors=("PROTOCOL_VERSION_UNSUPPORTED", "ALREADY_INITIALIZED", "SIDECAR_TOKEN_INVALID"),
        roles=ALL_ROLES,
    ),
    Method(name="shutdown", params="EmptyParams", result="EmptyResult", roles=ALL_ROLES),
    Method(name="app/snapshot", params="EmptyParams", result="SnapshotResult"),
    Method(
        name="events/subscribe",
        params="SubscribeParams",
        result="SubscribeResult",
        errors=("RESYNC_REQUIRED",),
    ),
    Method(name="events/unsubscribe", params="EmptyParams", result="EmptyResult"),
    Method(
        name="project/add",
        params="ProjectAddParams",
        result="ProjectView",
        errors=("PROJECT_NOT_A_GIT_REPO", "PATH_NOT_ALLOWED"),
    ),
    Method(name="project/remove", params="ProjectIdParams", result="EmptyResult", errors=("TAB_STATE_CONFLICT",)),
    Method(name="project/setActive", params="ProjectIdParams", result="EmptyResult", errors=("PROJECT_NOT_FOUND",)),
    Method(
        name="tab/open",
        params="OpenTabParams",
        result="TabView",
        errors=(
            "PROJECT_NOT_FOUND",
            "AGENT_UNAVAILABLE",
            "AGENT_NOT_LOGGED_IN",
            "WORKTREE_CREATE_FAILED",
            "BRANCH_ALREADY_CHECKED_OUT",
            "SESSION_NOT_FOUND",
        ),
        long=True,
    ),
    Method(
        name="tab/launchSpec",
        params="TabIdParams",
        result="TerminalLaunchSpec",
        errors=("TAB_NOT_FOUND", "CONTAINER_START_FAILED"),
    ),
    Method(name="tab/started", params="StartedParams", result="EmptyResult", errors=("TAB_NOT_FOUND",)),
    Method(name="tab/exited", params="ExitedParams", result="EmptyResult", errors=("TAB_NOT_FOUND",)),
    Method(
        name="tab/close",
        params="CloseTabParams",
        result="CloseTabResult",
        errors=("TAB_NOT_FOUND", "WORKTREE_DIRTY"),
    ),
    Method(
        name="tab/restart",
        params="RestartParams",
        result="TabView",
        errors=("TAB_NOT_FOUND", "CONTAINER_START_FAILED"),
    ),
    Method(name="tab/fork", params="TabIdParams", result="TabView", errors=("TAB_NOT_FOUND",)),
    Method(name="tab/rename", params="RenameParams", result="EmptyResult", errors=("TAB_NOT_FOUND",)),
    Method(name="tab/reorder", params="ReorderParams", result="EmptyResult", errors=("TAB_NOT_FOUND",)),
    Method(name="tab/focus", params="FocusParams", result="EmptyResult"),
    Method(
        name="approval/decide",
        params="DecideParams",
        result="AppliedResult",
        errors=("APPROVAL_NOT_FOUND", "APPROVAL_EXPIRED", "APPROVAL_NONCE_MISMATCH", "APPROVAL_ALREADY_DECIDED"),
    ),
    Method(
        name="approval/releaseToNative",
        params="ReleaseParams",
        result="ReleasedResult",
        errors=("APPROVAL_NOT_FOUND", "APPROVAL_ALREADY_DECIDED", "APPROVAL_NONCE_MISMATCH"),
    ),
    Method(name="palette/items", params="PaletteQuery", result="PaletteItemsResult"),
    Method(name="palette/execute", params="PaletteExecuteParams", result="CommandResult"),
    Method(name="palette/resetRanking", params="ResetRankingParams", result="EmptyResult"),
    Method(name="session/list", params="SessionListParams", result="SessionListResult"),
    Method(name="plan/list", params="PlanListParams", result="PlanListResult"),
    Method(name="plan/get", params="PlanIdParams", result="PlanView", errors=("PLAN_NOT_FOUND",)),
    Method(
        name="plan/setNodeStatus",
        params="SetNodeStatusParams",
        result="PlanNodeView",
        errors=("NODE_TRANSITION_INVALID", "NODE_NOT_READY", "NODE_NOT_FOUND"),
    ),
    Method(
        name="knowledge/impact",
        params="ImpactParams",
        result="ImpactView",
        errors=("IMPACT_TARGET_UNKNOWN",),
        long=True,
    ),
    Method(name="knowledge/noteUri", params="NoteUriParams", result="NoteUriResult", errors=("NOTE_NOT_FOUND",)),
    Method(
        name="checkpoint/list",
        params="CheckpointListParams",
        result="CheckpointListResult",
        errors=("TAB_NOT_FOUND",),
        capability="checkpoints.v1",
    ),
    Method(
        name="checkpoint/restorePreview",
        params="CheckpointIdParams",
        result="RestorePreviewResult",
        errors=("CHECKPOINT_NOT_FOUND",),
        capability="checkpoints.v1",
    ),
    Method(
        name="checkpoint/restore",
        params="RestoreParams",
        result="RestoreResult",
        errors=("CHECKPOINT_NOT_FOUND", "CHECKPOINT_RESTORE_CONFLICT", "CONFIRM_TOKEN_INVALID"),
        long=True,
        capability="checkpoints.v1",
    ),
    Method(
        name="diff/summary",
        params="DiffSummaryParams",
        result="DiffSummaryResult",
        errors=("TAB_NOT_FOUND",),
        capability="diff.v1",
    ),
    Method(
        name="diff/file",
        params="DiffFileParams",
        result="DiffFileResult",
        errors=("DIFF_TOO_LARGE",),
        capability="diff.v1",
    ),
    Method(
        name="diff/sendComment",
        params="SendCommentParams",
        result="CommandResult",
        errors=("TAB_NOT_FOUND",),
        capability="diff.v1",
    ),
    Method(name="setup/checks", params="EmptyParams", result="ChecksResult", long=True),
    Method(name="setup/previewFix", params="FixIdParams", result="FixPreview", errors=("SETUP_FIX_UNKNOWN",)),
    Method(
        name="setup/applyFix",
        params="ApplyFixParams",
        result="CheckResult",
        errors=("SETUP_FIX_UNKNOWN", "SETUP_FIX_FAILED", "CONFIRM_TOKEN_INVALID"),
        long=True,
    ),
    Method(name="settings/get", params="EmptyParams", result="Settings"),
    Method(
        name="settings/update",
        params="SettingsUpdateParams",
        result="Settings",
        errors=("CONTENT_MODIFIED",),
    ),
    Method(name="app/quitCheck", params="EmptyParams", result="QuitCheckResult"),
    Method(name="app/stopAgents", params="EmptyParams", result="EmptyResult", long=True),
    Method(
        name="terminal/screenTextReply",
        params="ScreenTextReplyParams",
        result="EmptyResult",
        capability="terminal.screen_text",
    ),
)


def method_by_name(name: str) -> Method:
    for method in METHODS:
        if method.name == name:
            return method
    message = f"unknown method {name}"
    raise KeyError(message)
