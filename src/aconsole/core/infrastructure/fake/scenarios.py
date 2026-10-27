"""Snapshots and event bursts for `--fake demo|empty|stress|errors`."""

from datetime import UTC, datetime

import msgspec

from aconsole.contracts import API_VERSION
from aconsole.contracts.app import (
    AccountView,
    AgentInfo,
    ApprovalView,
    AppSnapshot,
    CheckpointView,
    CheckResult,
    CommandDetail,
    DiffFileView,
    DiffSummaryResult,
    EditDetail,
    PaletteItem,
    PlanNodeView,
    PlanView,
    ProjectView,
    QuotaWindowView,
    SetupState,
    TabView,
    TerminalLaunchSpec,
)
from aconsole.contracts.common import (
    AccountId,
    AgentKind,
    ApprovalId,
    CheckpointId,
    NodeId,
    NodeStatus,
    NoteRef,
    PaletteKind,
    PlanId,
    ProjectId,
    QuotaLevel,
    RiskTag,
    TabId,
    TabStatus,
)
from aconsole.contracts.errors import ERRORS, ErrorData, ErrorDetails, ErrorObject, error_by_name
from aconsole.contracts.events import (
    AgentsChanged,
    ApprovalRequested,
    ApprovalResolved,
    Banner,
    CheckpointCreated,
    InjectToTerminal,
    Notify,
    OperationProgress,
    PlanUpdated,
    ProjectsChanged,
    QuotaUpdated,
    ResyncRequired,
    ScreenTextRequested,
    SettingsChanged,
    SetupRequired,
    TabAdded,
    TabFocusRequested,
    TabRemoved,
    TabUpdated,
    Toast,
    UiEvent,
    VaultEditRejected,
)

AT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
APP_EVENT_TYPES = frozenset(
    {
        "tab_added",
        "tab_updated",
        "tab_removed",
        "tab_focus_requested",
        "approval_requested",
        "approval_resolved",
        "quota_updated",
        "plan_updated",
        "projects_changed",
        "agents_changed",
        "settings_changed",
        "notify",
        "toast",
        "banner",
        "inject_to_terminal",
        "checkpoint_created",
        "operation_progress",
        "vault_edit_rejected",
        "screen_text_requested",
        "resync_required",
        "setup_required",
    }
)
_SPIKE = "printf '\\033[?2026h\\033[32mhi\\033[0m\\033]8;;https://example.com\\033\\\\link\\033]8;;\\033\\\\\\a\\033]9;n\\a\\033[?2026l'"


def _ulid(number: int) -> str:
    return f"01JB2Z{number:020d}"


def _tab(number: int, status: TabStatus, agent: AgentKind, project: str) -> TabView:
    return TabView(
        id=TabId(_ulid(100 + number)),
        project_id=ProjectId(project),
        agent=agent,
        account_id=AccountId("claude:demo") if agent is AgentKind.CLAUDE else AccountId("codex:demo"),
        title=f"{agent.value} {number}",
        order=number,
        status=status,
        status_detail=None,
        status_since=AT,
        attention=status in {TabStatus.WAITING_APPROVAL, TabStatus.WAITING_INPUT, TabStatus.ERROR},
        last_message=None,
        worktree_path=f"/wt/{number}",
        branch=f"ac/demo-{number}",
        base_branch="main",
        worktree_dirty=False,
        ahead=0,
        behind=0,
        plan_id=None,
        plan_node=None,
        plan_node_title=None,
        context_used_pct=None,
        session_cost_usd=None,
        agent_session_id=None,
        pending_approvals=0,
        checkpoints=0,
        started=status is not TabStatus.STARTING,
    )


def _project(number: int, name: str) -> ProjectView:
    return ProjectView(
        id=ProjectId(_ulid(number)),
        name=name,
        root=f"/repo/{name}",
        default_branch="main",
        vault_folder=None,
        languages=frozenset({"python"}),
    )


def _account(agent: AgentKind, level: QuotaLevel, used: float) -> AccountView:
    ident = "claude:demo" if agent is AgentKind.CLAUDE else "codex:demo"
    window = QuotaWindowView(
        key="5h",
        used_pct=used,
        resets_at=AT,
        level=level,
        observed_at=AT,
        estimated=False,
    )
    return AccountView(id=AccountId(ident), agent=agent, plan_label="Pro", quota=(window,))


def _agent(kind: AgentKind) -> AgentInfo:
    return AgentInfo(
        kind=kind,
        display_name=kind.value,
        container=f"agent-{kind.value}",
        available=True,
        unavailable_reason=None,
        logged_in=True,
        version="0",
        supported=True,
        capabilities=frozenset({"hooks"}),
    )


def _nodes() -> tuple[PlanNodeView, ...]:
    statuses = (
        NodeStatus.DONE,
        NodeStatus.DONE,
        NodeStatus.IN_PROGRESS,
        NodeStatus.PENDING,
        NodeStatus.PENDING,
        NodeStatus.BLOCKED,
        NodeStatus.FAILED,
        NodeStatus.CANCELLED,
    )
    built: list[PlanNodeView] = []
    for index, status in enumerate(statuses, start=1):
        built.append(
            PlanNodeView(
                id=NodeId(_ulid(200 + index)),
                key=f"N-{index:02d}",
                title=f"Node {index}",
                status=status,
                ready=status is NodeStatus.PENDING,
                layer=index,
                priority=float(index),
                depends_on=(),
                owner_agent=None,
                tab_id=None,
                lease_expires_at=None,
                note=None,
            )
        )
    return tuple(built)


def _plan(project: str) -> PlanView:
    nodes = _nodes()
    return PlanView(
        id=PlanId(_ulid(300)),
        project_id=ProjectId(project),
        title="Demo",
        goal="Show the UI",
        status="active",
        nodes=nodes,
        done=2,
        total=len(nodes),
        canvas=None,
        updated_at=AT,
    )


def _approvals(tab: TabId) -> tuple[ApprovalView, ...]:
    command = ApprovalView(
        id=ApprovalId(_ulid(400)),
        nonce="n1",
        tab_id=tab,
        agent=AgentKind.CLAUDE,
        tool="Bash",
        summary="rm -rf build",
        detail=CommandDetail(command="rm -rf build", args_preview=None),
        cwd="/wt/1",
        risk=frozenset({RiskTag.DESTRUCTIVE}),
        requested_at=AT,
        expires_at=AT,
        allow_tab_session=True,
    )
    edit = ApprovalView(
        id=ApprovalId(_ulid(401)),
        nonce="n2",
        tab_id=tab,
        agent=AgentKind.CODEX,
        tool="Edit",
        summary="Изменить 3 файла",
        detail=EditDetail(files=("a.py", "b.py", "c.py"), diff_preview=None),
        cwd="/wt/2",
        risk=frozenset(),
        requested_at=AT,
        expires_at=AT,
        allow_tab_session=False,
    )
    return (command, edit)


_DEMO_TABS = 5
_DEMO_CLAUDE = 3


def _tabs(project: str, count: int) -> tuple[TabView, ...]:
    cycle = (
        TabStatus.STARTING,
        TabStatus.IDLE,
        TabStatus.WORKING,
        TabStatus.WAITING_APPROVAL,
        TabStatus.ERROR,
    )
    claude_count = _DEMO_CLAUDE if count == _DEMO_TABS else count
    built: list[TabView] = []
    for index in range(count):
        agent = AgentKind.CLAUDE if index < claude_count else AgentKind.CODEX
        built.append(_tab(index + 1, cycle[index % len(cycle)], agent, project))
    return tuple(built)


def demo_snapshot() -> AppSnapshot:
    project_a = _ulid(1)
    tabs = _tabs(project_a, 5)
    return AppSnapshot(
        api_version=API_VERSION,
        projects=(_project(1, "alpha"), _project(2, "beta")),
        active_project=ProjectId(project_a),
        agents=(_agent(AgentKind.CLAUDE), _agent(AgentKind.CODEX)),
        accounts=(_account(AgentKind.CLAUDE, QuotaLevel.OK, 10), _account(AgentKind.CODEX, QuotaLevel.WARN, 80)),
        tabs=tabs,
        approvals=_approvals(tabs[0].id),
        plans=(_plan(project_a),),
        vault_ok=False,
        setup=SetupState(required=False, failing_checks=0, warnings=0),
        settings_rev=1,
    )


def empty_snapshot() -> AppSnapshot:
    return AppSnapshot(
        api_version=API_VERSION,
        projects=(),
        active_project=None,
        agents=(),
        accounts=(),
        tabs=(),
        approvals=(),
        plans=(),
        vault_ok=False,
        setup=SetupState(required=True, failing_checks=2, warnings=1),
        settings_rev=0,
    )


def stress_snapshot() -> AppSnapshot:
    project = _ulid(1)
    return AppSnapshot(
        api_version=API_VERSION,
        projects=(_project(1, "stress"),),
        active_project=ProjectId(project),
        agents=(_agent(AgentKind.CLAUDE),),
        accounts=(_account(AgentKind.CLAUDE, QuotaLevel.OK, 1),),
        tabs=_tabs(project, 50),
        approvals=_approvals(TabId(_ulid(101))),
        plans=(),
        vault_ok=True,
        setup=SetupState(required=False, failing_checks=0, warnings=0),
        settings_rev=1,
    )


def demo_events(snapshot: AppSnapshot) -> tuple[UiEvent, ...]:
    """Events whose fold from `empty_snapshot()` is `snapshot`.

    A remove or resolve is followed by the same add or request, so the snapshot
    still holds tab 0 and the pending approvals. `ResyncRequired` is last: the
    client does not apply it, and nothing after it has to change the snapshot.
    """
    tab = snapshot.tabs[0]
    tabs = snapshot.tabs
    approvals = snapshot.approvals
    checkpoint = CheckpointView(
        id=CheckpointId(_ulid(500)),
        tab_id=tab.id,
        turn=1,
        created_at=AT,
        files_changed=1,
        insertions=1,
        deletions=0,
        label="hello",
    )
    added = tuple(TabAdded(tab=item) for item in tabs)
    removed = tuple(TabRemoved(tab_id=item.id) for item in tabs)
    requested = tuple(ApprovalRequested(approval=item) for item in approvals)
    resolved = tuple(
        ApprovalResolved(approval_id=item.id, outcome="allowed", by="user") for item in approvals
    )
    return (
        *added,
        TabUpdated(tab=tab, changed=frozenset({"status"})),
        *removed,
        *added,
        TabFocusRequested(tab_id=tab.id),
        *requested,
        *resolved,
        *requested,
        *(QuotaUpdated(account=item) for item in snapshot.accounts),
        *(PlanUpdated(plan=item) for item in snapshot.plans),
        ProjectsChanged(projects=snapshot.projects, active_project=snapshot.active_project),
        AgentsChanged(agents=snapshot.agents),
        SettingsChanged(rev=snapshot.settings_rev),
        Notify(tab_id=tab.id, title="Wait", body="approval", priority="high", episode_id="ep-1"),
        Toast(message="saved", action_label=None, action_item_id=None),
        Banner(id="b1", message="core", button_label=None, button_item_id=None, visible=True),
        InjectToTerminal(tab_id=tab.id, text="ls\n", submit=True),
        CheckpointCreated(checkpoint=checkpoint),
        OperationProgress(operation_id="op-1", title="open", fraction=0.5, done=False, error=None),
        VaultEditRejected(note=NoteRef("plans/demo/N-01.md"), reason="cycle", restored=True),
        ScreenTextRequested(request_id="r1", tab_id=tab.id, last_lines=20),
        SetupRequired(setup=snapshot.setup),
        ResyncRequired(reason="restart"),
    )


def palette_items(count: int = 40) -> tuple[PaletteItem, ...]:
    return tuple(
        PaletteItem(
            id=f"cmd-{index}",
            kind=PaletteKind.COMMAND,
            title=f"Command {index}",
            subtitle=None,
            keywords=(),
            aliases=(),
            score_hint=0.0,
            enabled=True,
            disabled_reason=None,
            argument=None,
            shortcut=None,
        )
        for index in range(count)
    )


def launch_spec() -> TerminalLaunchSpec:
    return TerminalLaunchSpec(argv=("/bin/sh", "-c", _SPIKE), env={}, cwd="/wt", scrollback_lines=1000, title="spike")


def empty_checks() -> tuple[CheckResult, ...]:
    return (
        CheckResult(
            id="podman",
            title="podman",
            ok=False,
            severity="error",
            detail="missing",
            fix_id="install-podman",
            requires_confirmation=True,
        ),
        CheckResult(
            id="vault",
            title="vault",
            ok=False,
            severity="warning",
            detail="not configured",
            fix_id="pick-vault",
            requires_confirmation=False,
        ),
    )


def demo_diff() -> DiffSummaryResult:
    files = (
        DiffFileView(path="a.py", old_path=None, change="modified", insertions=1, deletions=0),
        DiffFileView(path="b.py", old_path=None, change="added", insertions=2, deletions=0),
        DiffFileView(path="c.py", old_path="d.py", change="renamed", insertions=0, deletions=1),
    )
    return DiffSummaryResult(base="main", head="ac/demo-1", files=files, insertions=3, deletions=1)


def demo_checkpoints(tab_id: TabId) -> tuple[CheckpointView, ...]:
    return tuple(
        CheckpointView(
            id=CheckpointId(_ulid(600 + index)),
            tab_id=tab_id,
            turn=index,
            created_at=AT,
            files_changed=1,
            insertions=1,
            deletions=0,
            label=f"turn {index}",
        )
        for index in range(1, 7)
    )


def waiting_snapshot() -> AppSnapshot:
    base = demo_snapshot()
    project = str(base.projects[0].id)
    statuses = (TabStatus.WAITING_INPUT, TabStatus.UNKNOWN, TabStatus.EXITED)
    tabs = tuple(_tab(index + 1, statuses[index], AgentKind.CLAUDE, project) for index in range(len(statuses)))
    return msgspec.structs.replace(base, tabs=tabs)


def quota_critical_snapshot() -> AppSnapshot:
    base = demo_snapshot()
    accounts = (
        _account(AgentKind.CLAUDE, QuotaLevel.CRITICAL, 99),
        _account(AgentKind.CODEX, QuotaLevel.UNKNOWN, 0),
    )
    return msgspec.structs.replace(base, accounts=accounts)


def named_error(name: str, details: ErrorDetails | None = None) -> ErrorObject:
    spec = error_by_name(name)
    return ErrorObject(
        code=spec.code,
        message=spec.name,
        data=ErrorData(
            reason=spec.name,
            domain="aconsole.ipc",
            retryable=spec.retryable,
            user_message_key=f"error.{spec.name.lower()}",
            details=details,
        ),
    )


def error_object(index: int) -> ErrorObject:
    spec = ERRORS[index % len(ERRORS)]
    return ErrorObject(
        code=spec.code,
        message=spec.name,
        data=ErrorData(
            reason=spec.name,
            domain="aconsole.core",
            retryable=spec.retryable,
            user_message_key=f"error.{spec.name.lower()}",
        ),
    )
