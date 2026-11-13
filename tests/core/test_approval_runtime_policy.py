"""Policy decisions precede UI availability and native permission fallbacks."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from aconsole.core.application.services.approval_broker import ApprovalHold
from aconsole.core.bootstrap import Daemon
from aconsole.core.domain.approval_policy import (
    ApprovalPolicyChoice,
    ApprovalPolicyMode,
    DangerousSubmode,
    PolicyOrigin,
)
from aconsole.core.domain.approvals import ApprovalBook
from aconsole.core.infrastructure.agents.claude.launcher import ClaudeLauncher, ClaudeTab
from aconsole.core.infrastructure.agents.launch import HostAgentCommands
from aconsole.core.infrastructure.http.approval import handle_permission
from aconsole.core.infrastructure.http.routes import IngestItem

if TYPE_CHECKING:
    from pathlib import Path


def _item(event: str = "PreToolUse", command: str = "touch marker") -> IngestItem:
    return IngestItem(tab_id="tab", kind="claude.hook", body=json.dumps({
        "hook_event_name": event, "session_id": "session", "tool_use_id": "call",
        "tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/work",
    }).encode())


@pytest.mark.parametrize("inline", [True, False])
@pytest.mark.parametrize("mode", ["allow", "deny"])
@pytest.mark.parametrize("event", ["PreToolUse", "PermissionRequest"])
def test_automatic_decision_needs_no_ui_and_is_audited(mode: str, event: str, *, inline: bool) -> None:
    audits = []
    events = []
    hold = ApprovalHold(ApprovalBook(), publish=events.append, notify=lambda _row: None,
                        audit=audits.append, now=lambda: datetime.now(UTC))
    response = asyncio.run(handle_permission(
        _item(event), hold, hold_seconds=30, worktree="/work", inline=inline,
        choice=ApprovalPolicyChoice(mode=ApprovalPolicyMode(mode)),
    ))
    output = json.loads(response.body)["hookSpecificOutput"]
    assert (output["permissionDecision"] if event == "PreToolUse" else output["decision"]["behavior"]) == mode
    assert len(audits) == 1
    assert audits[0].by.value == "rule"
    assert audits[0].reason == f"policy={mode}"
    assert hold.pending_views() == ()


def test_dangerous_allow_submode_holds_before_a_preapproved_tool_runs() -> None:
    async def scenario() -> None:
        from aconsole.contracts.app import DecideParams
        from aconsole.contracts.common import ApprovalDecision, ApprovalId, ApprovalScope

        hold = ApprovalHold(ApprovalBook(), publish=lambda _event: None, notify=lambda _row: None,
                            audit=lambda _row: None, now=lambda: datetime.now(UTC))
        task = asyncio.create_task(handle_permission(
            _item(command="rm -f marker"), hold, hold_seconds=30, worktree="/work",
            choice=ApprovalPolicyChoice(mode=ApprovalPolicyMode.ALLOW),
        ))
        await asyncio.sleep(0)
        assert not task.done()
        view, = hold.pending_views()
        hold.decide(DecideParams(approval_id=ApprovalId(str(view.id)), nonce=view.nonce,
                                 decision=ApprovalDecision.DENY, scope=ApprovalScope.ONCE), at=datetime.now(UTC))
        assert json.loads((await task).body)["hookSpecificOutput"]["permissionDecision"] == "deny"

    asyncio.run(scenario())


def test_project_override_applies_without_ui_or_inline_mode(tmp_path: Path) -> None:
    daemon = Daemon(tmp_path / "core.sock")
    daemon._permission_worktrees["tab"] = "/work"
    daemon._permission_projects["tab"] = "project"
    daemon._agent_commands._policy_tabs.add("tab")
    daemon._approval_policy.set_global(ApprovalPolicyChoice(mode=ApprovalPolicyMode.DENY), origin=PolicyOrigin.UI)
    daemon._approval_policy.set_project("project", ApprovalPolicyChoice(
        mode=ApprovalPolicyMode.ALLOW, dangerous=DangerousSubmode.ALLOW,
    ), origin=PolicyOrigin.UI)
    response = asyncio.run(daemon._permission(_item()))
    assert json.loads(response.body)["hookSpecificOutput"]["permissionDecision"] == "allow"
    daemon._approval_policy.set_project("project", None, origin=PolicyOrigin.UI)
    response = asyncio.run(daemon._permission(_item()))
    assert json.loads(response.body)["hookSpecificOutput"]["permissionDecision"] == "deny"
    daemon._agent_commands._policy_tabs.clear()
    daemon._approval_policy.set_global(ApprovalPolicyChoice(mode=ApprovalPolicyMode.ALLOW), origin=PolicyOrigin.UI)
    response = asyncio.run(daemon._permission(_item()))
    assert json.loads(response.body)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_unknown_tool_cannot_silently_pass_dangerous_ask() -> None:
    item = IngestItem(tab_id="tab", kind="claude.hook", body=json.dumps({
        "hook_event_name": "PreToolUse", "session_id": "session", "tool_name": "mcp__unknown__execute",
        "tool_input": {"payload": "arbitrary"},
    }).encode())
    hold = ApprovalHold(ApprovalBook(), publish=lambda _event: None, notify=lambda _row: None,
                        audit=lambda _row: None, now=lambda: datetime.now(UTC))
    response = asyncio.run(handle_permission(item, hold, hold_seconds=30, worktree="/work", inline=False,
                                           choice=ApprovalPolicyChoice(mode=ApprovalPolicyMode.ALLOW)))
    assert json.loads(response.body)["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_deny_launch_is_static_and_hook_failures_block(tmp_path: Path) -> None:
    launch = ClaudeLauncher().launch(ClaudeTab(
        agent_home=tmp_path, tab_id="tab", port=8765, token="test" + "-only",
        policy=ApprovalPolicyChoice(mode=ApprovalPolicyMode.DENY),
    ))
    settings = json.loads(launch.settings_path.read_bytes())
    assert settings["permissions"] == {"deny": ["*"], "defaultMode": "dontAsk"}
    for event in ("PreToolUse", "PermissionRequest"):
        assert settings["hooks"][event][0]["hooks"][0]["onFailure"] == "block"


@pytest.mark.parametrize("mode", ["allow", "deny"])
@pytest.mark.parametrize("failure", ["old-version", "no-home", "no-port", "settings-write"])
def test_automatic_policy_cannot_fall_back_to_an_unprotected_cli(tmp_path: Path, mode: str, failure: str) -> None:
    from aconsole.contracts.common import AgentKind

    if failure == "settings-write":
        (tmp_path / ".aconsole").write_text("not a directory")
    commands = HostAgentCommands(
        lambda _container: None if failure == "no-home" else tmp_path,
        approval_choice=lambda _tab: ApprovalPolicyChoice(mode=ApprovalPolicyMode(mode)),
        claude_version=lambda _container: "2.1.294" if failure == "old-version" else "2.1.318 (Claude Code)",
    )
    argv = commands.command(AgentKind.CLAUDE, "tab", "test-only", "agent-claude",
                            "0" if failure == "no-port" else "8765")
    assert argv is not None
    assert argv[:2] == ("/bin/sh", "-c")
    assert "exit 1" in argv[2]
    assert not commands.policy_ready("tab")


@pytest.mark.parametrize("mode", ["allow", "deny"])
def test_opencode_static_policy_requires_the_tested_version(mode: str) -> None:
    from aconsole.contracts.common import AgentKind

    commands = HostAgentCommands(lambda _container: None,
        approval_choice=lambda _tab: ApprovalPolicyChoice(mode=ApprovalPolicyMode(mode)),
        opencode_version=lambda _container: "1.0.0",
    )
    argv = commands.command(AgentKind.OPENCODE, "tab", "test-only", "agent-opencode", "8765")
    assert argv is not None
    assert argv[:2] == ("/bin/sh", "-c")
    assert "exit 1" in argv[2]
    assert "approval_policy_static" not in commands.capabilities(AgentKind.OPENCODE)


def test_opencode_launch_uses_the_project_policy_in_the_server_configuration() -> None:
    from aconsole.contracts.common import AgentKind

    commands = HostAgentCommands(lambda _container: None,
        approval_choice=lambda _tab: ApprovalPolicyChoice(mode=ApprovalPolicyMode.DENY),
        opencode_version=lambda _container: "opencode v2.1.2",
    )
    argv = commands.command(AgentKind.OPENCODE, "tab", "test-only", "agent-opencode", "8765")
    assert argv is not None
    config = json.loads(argv[4])
    assert config["permissions"] == [{"action": "*", "resource": "*", "effect": "deny"}]
    assert config["policies"] == [{"action": "tool.use", "resource": "*", "effect": "deny"}]
    assert "approval_policy_static" in commands.capabilities(AgentKind.OPENCODE)
    commands.release(AgentKind.OPENCODE, "tab", "agent-opencode")
    assert "approval_policy_static" not in commands.capabilities(AgentKind.OPENCODE)


@pytest.mark.parametrize("fallback", ["expire", "release", "ui-loss", "drop-tab"])
def test_codex_pretool_approval_never_falls_through_on_lost_ui(fallback: str) -> None:
    async def scenario() -> None:
        from datetime import timedelta

        from aconsole.contracts.app import ReleaseParams

        hold = ApprovalHold(ApprovalBook(), publish=lambda _event: None, notify=lambda _row: None,
                            audit=lambda _row: None, now=lambda: datetime.now(UTC))
        item = _item()
        task = asyncio.create_task(handle_permission(
            IngestItem(tab_id=item.tab_id, kind="codex.hook", body=item.body), hold,
            hold_seconds=30, worktree="/work",
        ))
        await asyncio.sleep(0)
        view, = hold.pending_views()
        if fallback == "expire":
            hold.expire(at=datetime.now(UTC) + timedelta(minutes=1))
        elif fallback == "release":
            hold.release_to_native(ReleaseParams(approval_id=view.id, nonce=view.nonce), at=datetime.now(UTC))
        elif fallback == "ui-loss":
            hold.release_pending()
        else:
            hold.drop_tab("tab")
        response = await task
        assert json.loads(response.body)["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert hold.pending_views() == ()

    asyncio.run(scenario())


def test_codex_dangerous_native_call_is_blocked_instead_of_unsupported_ask() -> None:
    item = _item(command="rm -f marker")
    hold = ApprovalHold(ApprovalBook(), publish=lambda _event: None, notify=lambda _row: None,
                        audit=lambda _row: None, now=lambda: datetime.now(UTC))
    response = asyncio.run(handle_permission(
        IngestItem(tab_id=item.tab_id, kind="codex.hook", body=item.body), hold, hold_seconds=30,
        worktree="/work", choice=ApprovalPolicyChoice(mode=ApprovalPolicyMode.ALLOW), inline=False,
    ))
    assert json.loads(response.body)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_codex_pretool_allow_does_not_approve_a_later_privilege_escalation() -> None:
    async def scenario() -> None:
        from aconsole.contracts.app import DecideParams
        from aconsole.contracts.common import ApprovalDecision, ApprovalScope

        hold = ApprovalHold(ApprovalBook(), publish=lambda _event: None, notify=lambda _row: None,
                            audit=lambda _row: None, now=lambda: datetime.now(UTC))
        item = _item(command="rm -f marker")
        choice = ApprovalPolicyChoice(mode=ApprovalPolicyMode.ALLOW)
        first = asyncio.create_task(handle_permission(
            IngestItem(tab_id=item.tab_id, kind="codex.hook", body=item.body), hold,
            hold_seconds=30, worktree="/work", choice=choice,
        ))
        await asyncio.sleep(0)
        view, = hold.pending_views()
        hold.decide(DecideParams(approval_id=view.id, nonce=view.nonce,
                                 decision=ApprovalDecision.ALLOW, scope=ApprovalScope.ONCE), at=datetime.now(UTC))
        assert json.loads((await first).body)["hookSpecificOutput"]["permissionDecision"] == "allow"
        item = _item(event="PermissionRequest", command="rm -f marker")
        second = asyncio.create_task(handle_permission(
            IngestItem(tab_id=item.tab_id, kind="codex.hook", body=item.body), hold,
            hold_seconds=30, worktree="/work", choice=choice,
        ))
        await asyncio.sleep(0)
        assert not second.done()
        escalation, = hold.pending_views()
        assert escalation.id != view.id
        hold.decide(DecideParams(approval_id=escalation.id, nonce=escalation.nonce,
                                 decision=ApprovalDecision.DENY, scope=ApprovalScope.ONCE), at=datetime.now(UTC))
        assert json.loads((await second).body)["hookSpecificOutput"]["decision"]["behavior"] == "deny"

    asyncio.run(scenario())
