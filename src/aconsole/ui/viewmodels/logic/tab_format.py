"""Presentation of tab contract data for sidebar rows."""

from dataclasses import dataclass
from datetime import datetime

from aconsole.contracts.app import AccountView, TabView
from aconsole.contracts.common import TabStatus

_CENT = 0.01
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
    waiting_minutes: int | None
    status_note: str = ""


def session_cost(tab: TabView) -> str:
    """What the session has cost at API prices, as the agent counts it. Nothing below a cent, nothing unknown."""
    cost = tab.session_cost_usd
    return f"${cost:,.2f}" if cost is not None and cost >= _CENT else ""


def format_tab(tab: TabView, _accounts: tuple[AccountView, ...], now: datetime) -> TabPresentation:
    """Convert a tab snapshot into the visible row fields."""
    section_title = tab.work_session_title or tab.agent.value.upper()
    return TabPresentation(
        section_key=(
            f"session:{tab.work_session_id}" if tab.work_session_id else f"{tab.agent.value}:{tab.account_id or ''}"
        ),
        section_title=section_title,
        title=tab.title,
        subtitle=" · ".join(value for value in (tab.branch, tab.plan_node_title, session_cost(tab)) if value),
        detail=tab.last_message or "",
        status_symbol=_SYMBOLS[tab.status],
        spinning=tab.status is TabStatus.WORKING,
        attention=tab.attention,
        waiting_minutes=(
            max(0, int((now - tab.status_since).total_seconds()) // 60)
            if tab.status in (TabStatus.WAITING_APPROVAL, TabStatus.WAITING_INPUT) else None
        ),
        status_note="tabs.status.answer_in_terminal" if tab.status_detail == "ANSWER_IN_TERMINAL" else "",
    )
