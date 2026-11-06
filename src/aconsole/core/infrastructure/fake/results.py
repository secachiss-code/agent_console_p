"""One sample of every METHODS result, so the fake answers with the real type."""

import msgspec

from aconsole.contracts.app import (
    AppliedResult,
    CheckpointListResult,
    CheckResult,
    ChecksResult,
    CloseTabResult,
    CommandResult,
    EmptyResult,
    FixPreview,
    HealthResult,
    ImpactView,
    NoteUriResult,
    PaletteItemsResult,
    PlanListResult,
    PreparedImagePaste,
    QuitCheckResult,
    ReleasedResult,
    RestorePreviewResult,
    RestoreResult,
    SessionListResult,
    SnapshotResult,
    SubscribeResult,
)
from aconsole.contracts.common import TabId, WorkspaceMode
from aconsole.contracts.git import (
    CleanupWorktreeResult,
    FactsToReviewResult,
    GitChangeResult,
    GitGraphResult,
    GitOperationResult,
    GitOperationView,
    GitStateResult,
    MergePreviewResult,
    MergeProbeView,
    RestoreFilePreviewResult,
    SendDiffPreviewResult,
)
from aconsole.contracts.ipc import InitializeResult, ProtocolLimits, ProtocolVersion
from aconsole.contracts.methods import METHODS
from aconsole.contracts.settings import Settings
from aconsole.contracts.work_sessions import (
    WorkSessionHistoryResult,
    WorkSessionListResult,
    WorkSessionRestorePreview,
    WorkSessionView,
)
from aconsole.core.infrastructure.fake.scenarios import (
    AT,
    demo_checkpoints,
    demo_diff,
    demo_diff_file,
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
    change = GitChangeResult(ok=True, head_oid="0" * 40, detail=None)
    operation = GitOperationResult(
        operation=GitOperationView(
            id="op",
            kind="merge",
            status="finished",
            mode="merge",
            source="ac/demo-1",
            target="main",
            source_oid="1" * 40,
            target_oid="0" * 40,
            result_oid="2" * 40,
            tab_id=tab.id,
            plan_node=None,
            message=None,
            follow_up_pending=False,
            created_at=AT,
            updated_at=AT,
        ),
    )
    session = WorkSessionView(
        id="demo-task", project_id=project.id, title="Demo task", worktree=tab.worktree_path,
        branch=tab.branch, base_branch=project.default_branch, workspace=WorkspaceMode.NEW_WORKTREE,
        status="active", created_at=AT, updated_at=AT,
    )
    return {
        "workSession/create": session,
        "workSession/list": WorkSessionListResult(sessions=()),
        "workSession/get": session,
        "workSession/rename": session,
        "workSession/close": msgspec.structs.replace(session, status="archived"),
        "workSession/resume": session,
        "workSession/handoff": tab,
        "workSession/history": WorkSessionHistoryResult(snapshots=()),
        "workSession/result": WorkSessionHistoryResult(snapshots=()),
        "workSession/restore": WorkSessionHistoryResult(snapshots=()),
        "workSession/restorePreview": WorkSessionRestorePreview(sequence=1, oid="0" * 40, confirm_token="demo"  # noqa: S106 -- fake preview token
),
        "workSession/integratePreview": MergePreviewResult(
            source="worker", target="task", source_oid="1" * 40, target_oid="0" * 40,
            probe=MergeProbeView(status="clean", source_oid="1" * 40, target_oid="0" * 40),
            modes=("merge",), source_dirty=False, target_worktree=tab.worktree_path, blockers=(), confirm_token="demo"  # noqa: S106 -- fake preview token
,
        ),
        "workSession/integrate": operation,
        "workSession/cleanupWorker": empty,
        "initialize": InitializeResult(
            protocol=ProtocolVersion(major=1, minor=0),
            server_version="0.0.0",
            server_pid=1,
            session_id="01JB2Z00000000000000000099",
            capabilities=frozenset({"events.v1", "work_sessions.v1"}),
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
        "terminal/prepareImagePaste": PreparedImagePaste(path="/wt/1/.aconsole/clipboard/image.png"),
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
        "diff/file": demo_diff_file(),
        "diff/sendComment": command,
        "git/state": GitStateResult(
            project_id=project.id,
            revision="0",
            root=project.root,
            empty=False,
            base_branch=project.default_branch,
            base_oid="0" * 40,
            worktrees=(),
            branches=(),
            operations=(),
        ),
        "git/watch": EmptyResult(),
        "git/graph": GitGraphResult(version="0", nodes=(), next_cursor=None),
        "git/diffSummary": demo_diff(),
        "git/diffFile": demo_diff_file(),
        "git/stage": change,
        "git/unstage": change,
        "git/commit": change,
        "git/restoreFilePreview": RestoreFilePreviewResult(
            path="a.py",
            source_oid="0" * 40,
            insertions=0,
            deletions=0,
            exists_in_source=True,
            confirm_token="yes",  # noqa: S106
        ),
        "git/restoreFile": change,
        "git/mergePreview": MergePreviewResult(
            source="ac/demo-1",
            target="main",
            source_oid="1" * 40,
            target_oid="0" * 40,
            probe=MergeProbeView(status="clean", source_oid="1" * 40, target_oid="0" * 40),
            modes=("merge",),
            source_dirty=False,
            target_worktree=None,
            blockers=(),
            confirm_token="yes",  # noqa: S106
        ),
        "git/mergeWorktree": operation,
        "git/operation": operation,
        "git/cleanupWorktree": CleanupWorktreeResult(removed=True, branch_deleted=True, kept_ref=None),
        "git/factsToReview": FactsToReviewResult(head_oid="0" * 40, facts=()),
        "git/rebase": operation,
        "git/cherryPick": operation,
        "git/continue": change,
        "git/abort": change,
        "git/sendDiffPreview": SendDiffPreviewResult(
            to_tab=tab.id,
            to_title="tab",
            files=1,
            byte_count=1,
            confirm_token="yes",  # noqa: S106
        ),
        "git/sendDiff": command,
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
        "quota/refresh": EmptyResult(),
        "app/health": HealthResult(
            database=True,
            writer_queued=0,
            writer_failed=0,
            writer_last_commit_ms=1.0,
            ipc_answered=1,
            ipc_slowest_ms=1.0,
        ),
        "app/stopAgents": empty,
        "terminal/screenTextReply": empty,
    }
