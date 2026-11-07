"""Calls and text of the approval panel. No GTK here."""

import unicodedata
from datetime import datetime

from aconsole.contracts.app import AppliedResult, ApprovalView, DecideParams, ReleasedResult, ReleaseParams
from aconsole.contracts.common import ApprovalDecision, ApprovalScope, RiskTag
from aconsole.ui.client.core_client import CoreClient

_TEXT_LIMIT = 4000
_MINUTE = 60
_KEPT_CONTROLS = frozenset("\n\t")
# Codes for a request that no longer waits: not found, expired, wrong nonce, decided elsewhere.
CLOSED_CODES = frozenset({3001, 3002, 3003, 3004})
# Network alone is not here: most of what an agent runs reaches for it.
_RISKY = frozenset({
    RiskTag.DESTRUCTIVE, RiskTag.OUTSIDE_WORKTREE, RiskTag.PUBLISH, RiskTag.SECRETS, RiskTag.PRIVILEGE,
})


class ApprovalsViewModel:
    def __init__(self, client: CoreClient) -> None:
        self.client: CoreClient = client

    async def decide(
        self, view: ApprovalView, decision: ApprovalDecision, scope: ApprovalScope, *, message: str | None = None,
    ) -> AppliedResult:
        params = DecideParams(approval_id=view.id, nonce=view.nonce, decision=decision, scope=scope, message=message)
        return await self.client.request("approval/decide", params, AppliedResult)

    async def release(self, view: ApprovalView) -> ReleasedResult:
        params = ReleaseParams(approval_id=view.id, nonce=view.nonce)
        return await self.client.request("approval/releaseToNative", params, ReleasedResult)


def is_risky(view: ApprovalView) -> bool:
    return not _RISKY.isdisjoint(view.risk)


def safe_text(text: str, limit: int = _TEXT_LIMIT) -> str:
    """What the agent sent, unable to hide anything from the person deciding.

    Terminal escapes and direction overrides can make a command read as a different one, so they
    are shown as code points. A cut is marked: an unmarked one would hide the end of a command.
    """
    shown = "".join(_visible(char) for char in text)
    if len(shown) <= limit:
        return shown
    return f"{shown[:limit]}… (+{len(shown) - limit})"


def _visible(char: str) -> str:
    if char in _KEPT_CONTROLS or unicodedata.category(char) not in {"Cc", "Cf"}:
        return char
    return f"[U+{ord(char):04X}]"


def time_left(expires_at: datetime, now: datetime) -> str | None:
    """`m:ss` until the request goes back to the agent. None once the time is up."""
    seconds = int((expires_at - now).total_seconds())
    if seconds <= 0:
        return None
    return f"{seconds // _MINUTE}:{seconds % _MINUTE:02d}"
