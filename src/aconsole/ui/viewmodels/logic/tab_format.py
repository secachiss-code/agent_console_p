"""Presentation of tab contract data for sidebar rows."""

from dataclasses import dataclass
from datetime import datetime

from aconsole.contracts.app import AccountView, TabView
from aconsole.contracts.common import TabStatus

_SYMBOLS = {
    TabStatus.STARTING: "⟳",
    TabStatus.WORKING: "⟳",
    TabStatus.IDLE: "○",
    TabStatus.WAITING_APPROVAL: "◉",
    TabStatus.WAITING_INPUT: "◉",
    TabStatus.ERROR: "✕",
    TabStatus.EXITED: "◌",
    TabStatus.UNKNOWN: "?",
}


@dataclass(frozen=True, slots=True)
class TabPresentation:
    section_key: str
    section_title: str
    title: str
    subtitle: str
    detail: str
    status_symbol: str
    spinning: bool
    attention: bool


def format_tab(tab: TabView, accounts: tuple[AccountView, ...], now: datetime) -> TabPresentation:
    """Convert a tab snapshot into the visible row fields."""
    del now  # Reserved for a future waiting-duration presentation.
    account = next((item for item in accounts if item.id == tab.account_id), None)
    section_title = tab.agent.value.upper()
    if account is not None and account.plan_label:
        section_title = f"{section_title} · {account.plan_label}"
    return TabPresentation(
        section_key=f"{tab.agent.value}:{tab.account_id or ''}",
        section_title=section_title,
        title=tab.title,
        subtitle=" · ".join(value for value in (tab.branch, tab.plan_node_title) if value),
        detail=tab.last_message or "",
        status_symbol=_SYMBOLS[tab.status],
        spinning=tab.status is TabStatus.WORKING,
        attention=tab.attention,
    )
