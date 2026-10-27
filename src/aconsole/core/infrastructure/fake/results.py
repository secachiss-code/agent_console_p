"""One sample of every METHODS result, so the fake answers with the real type."""

import msgspec

from aconsole.contracts.app import (
    AppliedResult,
    CheckpointListResult,
    CheckResult,
    ChecksResult,
    CloseTabResult,
    CommandResult,
    DiffFileResult,
    EmptyResult,
    FixPreview,
    ImpactView,
    NoteUriResult,
    PaletteItemsResult,
    PlanListResult,
    QuitCheckResult,
    ReleasedResult,
    RestorePreviewResult,
    RestoreResult,
    SessionListResult,
    SnapshotResult,
    SubscribeResult,
)
from aconsole.contracts.common import TabId
from aconsole.contracts.ipc import InitializeResult, ProtocolLimits, ProtocolVersion
from aconsole.contracts.methods import METHODS
from aconsole.contracts.settings import Settings
from aconsole.core.infrastructure.fake.scenarios import (
    AT,
    demo_checkpoints,
    demo_diff,
    demo_snapshot,
    empty_checks,
    launch_spec,
    palette_items,
)


def result_type(method: str) -> type[msgspec.Struct] | None:
    sample = result_for(method)
    if sample is None:
        return None
    return type(sample)


def result_for(method: str) -> msgspec.Struct | None:
    return _samples().get(method)


def covers_methods() -> bool:
    return set(_samples()) == {method.name for method in METHODS}


def _samples() -> dict[str, msgspec.Struct]:
    snap = demo_snapshot()
    tab = snap.tabs[0]
    project = snap.projects[0]
    plan = snap.plans[0]
    node = plan.nodes[0]
    point = demo_checkpoints(tab.id)[0]
    settings = Settings(rev=1, vault_path="/vault", worktrees_dir="/wt")
    empty = EmptyResult()
    command = CommandResult(ok=True, toast=None)
    check = CheckResult(
        id="git",
        title="git",
        ok=True,
        severity="info",
        detail=None,
        fix_id=None,
        requires_confirmation=False,
    )
    return {
        "initialize": InitializeResult(
            protocol=ProtocolVersion(major=1, minor=0),
            server_version="0.0.0",
            server_pid=1,
            session_id="01JB2Z00000000000000000099",
            capabilities=frozenset({"events.v1"}),
            limits=ProtocolLimits(),
        ),
        "shutdown": empty,
        "app/snapshot": SnapshotResult(snapshot=snap, seq=0),
        "events/subscribe": SubscribeResult(current_seq=0),
        "events/unsubscribe": empty,
        "project/add": project,
        "project/remove": empty,
        "project/setActive": empty,
        "tab/open": tab,
        "tab/launchSpec": launch_spec(),
        "tab/started": empty,
        "tab/exited": empty,
        "tab/close": CloseTabResult(closed=True, needs_decision=False, dirty_summary=None),
        "tab/restart": tab,
        "tab/fork": tab,
        "tab/rename": empty,
        "tab/reorder": empty,
        "tab/focus": empty,
        "approval/decide": AppliedResult(applied=True),
        "approval/releaseToNative": ReleasedResult(released=True),
        "palette/items": PaletteItemsResult(items=palette_items(1)),
        "palette/execute": command,
        "palette/resetRanking": empty,
        "session/list": SessionListResult(sessions=()),
        "plan/list": PlanListResult(plans=(plan,)),
        "plan/get": plan,
        "plan/setNodeStatus": node,
        "knowledge/impact": ImpactView(target="mod", hops=1, layers=(("mod",),), truncated=False, canvas=None),
        "knowledge/noteUri": NoteUriResult(uri="file:///vault/n.md"),
        "checkpoint/list": CheckpointListResult(checkpoints=demo_checkpoints(TabId(tab.id))),
        "checkpoint/restorePreview": RestorePreviewResult(
            diffstat="1 file",
            files=("a.py",),
            confirm_token="yes",  # noqa: S106
            scope_note="tracked files",
        ),
        "checkpoint/restore": RestoreResult(restored=True, safety_checkpoint=point.id),
        "diff/summary": demo_diff(),
        "diff/file": DiffFileResult(hunks=(), truncated=False),
        "diff/sendComment": command,
        "setup/checks": ChecksResult(checks=empty_checks()),
        "setup/previewFix": FixPreview(
            fix_id="git",
            summary="install",
            diff=None,
            commands=("git init",),
            reversible=True,
            confirm_token="yes",  # noqa: S106
            expires_at=AT,
        ),
        "setup/applyFix": check,
        "settings/get": settings,
        "settings/update": settings,
        "app/quitCheck": QuitCheckResult(busy_tabs=(), pending_approvals=0),
        "app/stopAgents": empty,
        "terminal/screenTextReply": empty,
    }
