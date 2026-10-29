"""Independent tests for the tab row presentation contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import msgspec
import pytest

from aconsole.contracts.common import AgentKind, TabStatus
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot

if TYPE_CHECKING:
    from aconsole.contracts.app import TabView


NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _StatusCase:
    status: TabStatus
    symbol: str
    spinning: bool


@dataclass(frozen=True)
class _AttentionCase:
    status: TabStatus
    attention: bool


def _demo_tab(status: TabStatus = TabStatus.IDLE) -> TabView:
    snapshot = demo_snapshot()
    return msgspec.structs.replace(snapshot.tabs[0], status=status)


@pytest.mark.parametrize(
    "case",
    [
        _StatusCase(TabStatus.STARTING, "⟳", spinning=False),
        _StatusCase(TabStatus.IDLE, "○", spinning=False),
        _StatusCase(TabStatus.WORKING, "⟳", spinning=True),
        _StatusCase(TabStatus.WAITING_APPROVAL, "◉", spinning=False),
        _StatusCase(TabStatus.WAITING_INPUT, "◉", spinning=False),
        _StatusCase(TabStatus.ERROR, "✕", spinning=False),
        _StatusCase(TabStatus.EXITED, "◌", spinning=False),
        _StatusCase(TabStatus.UNKNOWN, "?", spinning=False),
    ],
)
def test_b2_tab_format_status(case: _StatusCase) -> None:
    from aconsole.ui.viewmodels.logic.tab_format import format_tab

    snapshot = demo_snapshot()
    tab = msgspec.structs.replace(snapshot.tabs[0], status=case.status)
    account = next(item for item in snapshot.accounts if item.id == tab.account_id)

    presentation = format_tab(tab, (account,), NOW)

    assert presentation.status_symbol == case.symbol
    assert presentation.spinning is case.spinning


@pytest.mark.parametrize(
    "case",
    [
        _AttentionCase(TabStatus.IDLE, attention=False),
        _AttentionCase(TabStatus.WAITING_APPROVAL, attention=True),
        _AttentionCase(TabStatus.WAITING_INPUT, attention=True),
        _AttentionCase(TabStatus.ERROR, attention=True),
        # The formatter reflects the contract value rather than inferring it.
        _AttentionCase(TabStatus.WORKING, attention=True),
    ],
)
def test_b2_tab_format_attention_comes_from_tab(case: _AttentionCase) -> None:
    from aconsole.ui.viewmodels.logic.tab_format import format_tab

    snapshot = demo_snapshot()
    tab = msgspec.structs.replace(
        snapshot.tabs[0], status=case.status, attention=case.attention
    )
    account = next(item for item in snapshot.accounts if item.id == tab.account_id)

    assert format_tab(tab, (account,), NOW).attention is case.attention


def test_b2_tab_format_groups_by_agent_and_account_and_shows_existing_plan_label() -> None:
    from aconsole.ui.viewmodels.logic.tab_format import format_tab

    snapshot = demo_snapshot()
    tab = snapshot.tabs[0]
    account = next(item for item in snapshot.accounts if item.id == tab.account_id)

    presentation = format_tab(tab, (account,), NOW)

    assert presentation.section_key == f"{tab.agent.value.lower()}:{tab.account_id}"
    assert presentation.section_title == f"{tab.agent.value.upper()} · {account.plan_label}"


def test_b2_tab_format_missing_account_has_no_invented_tariff() -> None:
    from aconsole.ui.viewmodels.logic.tab_format import format_tab

    tab = msgspec.structs.replace(_demo_tab(), account_id=None)
    presentation = format_tab(tab, (), NOW)

    assert presentation.section_key == f"{tab.agent.value.lower()}:"
    assert presentation.section_title == tab.agent.value.upper()


def test_b2_tab_format_empty_plan_label_does_not_add_separator() -> None:
    from aconsole.ui.viewmodels.logic.tab_format import format_tab

    snapshot = demo_snapshot()
    tab = snapshot.tabs[0]
    account = msgspec.structs.replace(
        next(item for item in snapshot.accounts if item.id == tab.account_id),
        plan_label="",
    )

    presentation = format_tab(tab, (account,), NOW)

    assert presentation.section_title == tab.agent.value.upper()


def test_b2_tab_format_subtitle_joins_nonempty_branch_and_plan_node() -> None:
    from aconsole.ui.viewmodels.logic.tab_format import format_tab

    tab = msgspec.structs.replace(
        _demo_tab(), branch="feature/demo", plan_node_title="Review API"
    )
    snapshot = demo_snapshot()
    account = next(item for item in snapshot.accounts if item.id == tab.account_id)

    assert format_tab(tab, (account,), NOW).subtitle == "feature/demo · Review API"


@pytest.mark.parametrize(
    ("branch", "node_title", "expected"),
    [
        (None, None, ""),
        ("", "", ""),
        ("main", None, "main"),
        (None, "Build UI", "Build UI"),
        ("", "Build UI", "Build UI"),
        ("main", "", "main"),
    ],
)
def test_b2_tab_format_subtitle_omits_empty_fields(
    branch: str | None, node_title: str | None, expected: str
) -> None:
    from aconsole.ui.viewmodels.logic.tab_format import format_tab

    tab = msgspec.structs.replace(
        _demo_tab(), branch=branch, plan_node_title=node_title
    )
    snapshot = demo_snapshot()
    account = next(item for item in snapshot.accounts if item.id == tab.account_id)

    assert format_tab(tab, (account,), NOW).subtitle == expected


@pytest.mark.parametrize(
    ("last_message", "expected"),
    [(None, ""), ("", ""), ("Working on the tests", "Working on the tests")],
)
def test_b2_tab_format_detail_uses_last_message_or_empty(
    last_message: str | None, expected: str
) -> None:
    from aconsole.ui.viewmodels.logic.tab_format import format_tab

    tab = msgspec.structs.replace(_demo_tab(), last_message=last_message)
    snapshot = demo_snapshot()
    account = next(item for item in snapshot.accounts if item.id == tab.account_id)

    assert format_tab(tab, (account,), NOW).detail == expected


def test_b2_tab_format_unknown_account_does_not_use_another_accounts_plan() -> None:
    from aconsole.ui.viewmodels.logic.tab_format import format_tab

    snapshot = demo_snapshot()
    tab = msgspec.structs.replace(
        snapshot.tabs[0], account_id="claude:missing", agent=AgentKind.CLAUDE
    )
    codex_account = next(
        item for item in snapshot.accounts if item.agent is AgentKind.CODEX
    )

    presentation = format_tab(tab, (codex_account,), NOW)

    assert presentation.section_key == "claude:claude:missing"
    assert presentation.section_title == "CLAUDE"
