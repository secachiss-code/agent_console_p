"""Which approval modes the window may offer, and what each agent actually does with one.

Ask, allow and deny have a decision. Scenario is stored and still asks, so the window shows it
and does not let a person choose it. A harness applies neither allow nor deny until its own adapter
does; the text says so. A project value replaces the global one. Null inherits it.
"""

from __future__ import annotations

from typing import Literal

PolicyMode = Literal["ask", "allow", "deny", "scenario"]
DangerousMode = Literal["ask", "allow"]

SELECTABLE: frozenset[str] = frozenset({"ask", "allow", "deny"})
SCENARIO_REASON = "prefs.policy_scenario_unavailable"
NO_HOOKS = "prefs.harness_no_hooks"
STILL_ASKS = "prefs.harness_asks"
NO_HOOKS_TAB = "tabs.no_hooks"


def mode_enabled(mode: str) -> bool:
    """Scenario stays visible and cannot be chosen. The other three have a decision."""
    return mode in SELECTABLE


def needs_dangerous_confirm(current: str, proposed: str) -> bool:
    """Turning dangerous calls on is a separate confirmation. Turning them off is not."""
    return proposed == "allow" and current != "allow"


def effective_policy(
    global_mode: str, global_dangerous: str, project_mode: str | None, project_dangerous: str | None,
) -> tuple[str, str]:
    """The project pair replaces the global pair. A missing project value inherits both."""
    if project_mode is None:
        return global_mode, global_dangerous
    return project_mode, "ask" if project_dangerous is None else project_dangerous


def automatic_banner_key(mode: str, dangerous: str) -> str | None:
    """A mode that would decide by itself stays visible. Ask and the scenario stub do not."""
    if mode == "deny":
        return "prefs.banner_deny"
    if mode == "allow" and dangerous == "allow":
        return "prefs.banner_allow_dangerous"
    if mode == "allow":
        return "prefs.banner_allow"
    return None


def harness_effect_key(capabilities: frozenset[str]) -> str:
    """What this CLI does today. Hooks can ask; none of them apply allow or deny yet."""
    if "approval_policy" in capabilities:
        return "prefs.harness_policy"
    if "approval_policy_static" in capabilities:
        return "prefs.harness_opencode_policy"
    if "approval_policy_codex" in capabilities:
        return "prefs.harness_codex_policy"
    if "hooks" not in capabilities:
        return NO_HOOKS
    return STILL_ASKS
