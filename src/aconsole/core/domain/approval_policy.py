"""Who decides an approval. `approvals_mode` only chooses where the question is shown.

A project choice replaces the global one. There is no tab level. `scenario` is stored and
answered as `ask` until a later node implements it. The UI does not offer it yet.

`allow` with `dangerous=ask` permits a call the classifier left untagged and asks when any
risk tag is present. `allow` with `dangerous=allow` permits the call even when tags are present.
`deny` refuses every call. Tags come from the classifier; this module does not classify.
"""

from dataclasses import dataclass
from enum import StrEnum

from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.risk import RiskTag


class ApprovalPolicyMode(StrEnum):
    ASK = "ask"
    ALLOW = "allow"
    DENY = "deny"
    SCENARIO = "scenario"


class DangerousSubmode(StrEnum):
    """What `allow` does with a tagged call. Ignored for every other mode."""

    ASK = "ask"
    ALLOW = "allow"


class PolicyVerdict(StrEnum):
    ASK = "ask"
    ALLOW = "allow"
    DENY = "deny"


class PolicyOrigin(StrEnum):
    """Who is asking to change the mode. Only the UI may."""

    UI = "ui"
    MCP = "mcp"
    INGEST = "ingest"
    CONTAINER = "container"


@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalPolicyChoice:
    mode: ApprovalPolicyMode = ApprovalPolicyMode.ASK
    dangerous: DangerousSubmode = DangerousSubmode.ASK


def choice_or_inherit(mode: str | None, dangerous: str | None) -> ApprovalPolicyChoice | None:
    """None when the project stores no override. An unknown stored value fails closed to ask."""
    if mode is None and dangerous is None:
        return None
    parsed_mode = ApprovalPolicyMode(mode) if mode in ApprovalPolicyMode else ApprovalPolicyMode.ASK
    parsed_dangerous = DangerousSubmode(dangerous) if dangerous in DangerousSubmode else DangerousSubmode.ASK
    return ApprovalPolicyChoice(mode=parsed_mode, dangerous=parsed_dangerous)


def stored_choice(mode: str, dangerous: str) -> ApprovalPolicyChoice:
    """The global choice. Missing or unknown values are ask."""
    found = choice_or_inherit(mode, dangerous)
    return ApprovalPolicyChoice() if found is None else found


def verdict(choice: ApprovalPolicyChoice, tags: frozenset[RiskTag]) -> PolicyVerdict:
    """The decision for one call. Scenario is the ask stub, covered by the same branch as ask."""
    if choice.mode is ApprovalPolicyMode.DENY:
        return PolicyVerdict.DENY
    if choice.mode is ApprovalPolicyMode.ALLOW and not (tags and choice.dangerous is DangerousSubmode.ASK):
        return PolicyVerdict.ALLOW
    return PolicyVerdict.ASK


def effective(global_choice: ApprovalPolicyChoice, project_choice: ApprovalPolicyChoice | None) -> ApprovalPolicyChoice:
    """The project choice replaces both the mode and the dangerous submode."""
    return global_choice if project_choice is None else project_choice


def require_ui(origin: PolicyOrigin) -> None:
    """MCP, ingest, and the container are refused. The mode changes only from the UI."""
    if origin is not PolicyOrigin.UI:
        raise DomainError(code=-32008, name="ROLE_FORBIDDEN", retryable=False)
