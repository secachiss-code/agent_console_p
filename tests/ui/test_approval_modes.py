"""The approval-mode window offers only modes that have a decision, and says what each agent does."""

from __future__ import annotations

from aconsole.contracts.app import AgentInfo
from aconsole.contracts.common import AgentKind
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
from aconsole.ui.viewmodels.logic.approval_modes import (
    NO_HOOKS,
    NO_HOOKS_TAB,
    STILL_ASKS,
    automatic_banner_key,
    effective_policy,
    harness_effect_key,
    mode_enabled,
    needs_dangerous_confirm,
)
from aconsole.ui.viewmodels.logic.tab_format import format_tab


def test_scenario_is_shown_but_cannot_be_chosen() -> None:
    assert mode_enabled("ask")
    assert mode_enabled("allow")
    assert mode_enabled("deny")
    assert not mode_enabled("scenario")


def test_dangerous_allow_asks_before_it_turns_on() -> None:
    assert needs_dangerous_confirm("ask", "allow")
    assert not needs_dangerous_confirm("allow", "allow")
    assert not needs_dangerous_confirm("allow", "ask")


def test_a_project_override_replaces_the_global_choice_and_null_inherits_it() -> None:
    assert effective_policy("deny", "ask", None, None) == ("deny", "ask")
    assert effective_policy("deny", "ask", "allow", "allow") == ("allow", "allow")
    assert effective_policy("ask", "ask", "allow", None) == ("allow", "ask")


def test_an_automatic_mode_has_a_permanent_label_and_ask_does_not() -> None:
    assert automatic_banner_key("ask", "ask") is None
    assert automatic_banner_key("scenario", "allow") is None
    assert automatic_banner_key("deny", "ask") == "prefs.banner_deny"
    assert automatic_banner_key("allow", "ask") == "prefs.banner_allow"
    assert automatic_banner_key("allow", "allow") == "prefs.banner_allow_dangerous"


def test_each_harness_says_what_this_cli_really_does() -> None:
    assert harness_effect_key(frozenset()) == NO_HOOKS
    assert harness_effect_key(frozenset({"resume"})) == NO_HOOKS
    assert harness_effect_key(frozenset({"hooks", "approvals"})) == STILL_ASKS
    assert harness_effect_key(frozenset({"hooks", "approval_policy"})) == "prefs.harness_policy"
    assert harness_effect_key(frozenset({"approval_policy_static"})) == "prefs.harness_opencode_policy"
    assert harness_effect_key(frozenset({"approval_policy_codex"})) == "prefs.harness_codex_policy"
    assert harness_effect_key(frozenset({"approval_policy_unavailable"})) == "prefs.harness_cursor_policy_unavailable"


def test_a_tab_without_hooks_says_so() -> None:
    snapshot = demo_snapshot()
    tab = snapshot.tabs[0]
    silent = AgentInfo(
        kind=tab.agent, display_name="Cursor", container="agent-cursor", available=True,
        unavailable_reason=None, logged_in=True, version="1", supported=True, capabilities=frozenset(),
    )
    hooked = AgentInfo(
        kind=AgentKind.CLAUDE, display_name="Claude", container="agent-claude", available=True,
        unavailable_reason=None, logged_in=True, version="1", supported=True,
        capabilities=frozenset({"hooks"}),
    )
    assert format_tab(tab, (), tab.status_since, (silent,)).hooks_note == NO_HOOKS_TAB
    claude = format_tab(tab, (), tab.status_since, (hooked,))
    assert claude.hooks_note == ""
