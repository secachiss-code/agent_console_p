"""Sidebar summary projection follows active-project and account state."""

from __future__ import annotations

import msgspec


def test_b4_sidebar_summary_filters_plan_and_tracks_quota_and_snapshot_updates() -> None:
    from aconsole.contracts.common import PlanId
    from aconsole.contracts.events import QuotaUpdated
    from aconsole.contracts.ipc import EventEnvelope
    from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
    from aconsole.ui.client.core_client import CoreClient
    from aconsole.ui.viewmodels.sidebar_vm import SidebarSummaryViewModel

    source = demo_snapshot()
    active_plan = source.plans[0]
    beta_project = source.projects[1].id
    beta_plan = msgspec.structs.replace(
        active_plan,
        id=PlanId("beta-plan"),
        project_id=beta_project,
        title="Beta active plan",
    )
    completed_plan = msgspec.structs.replace(
        active_plan,
        id=PlanId("completed-plan"),
        status="completed",
        title="Completed plan",
    )
    snapshot = msgspec.structs.replace(
        source,
        plans=(completed_plan, beta_plan, active_plan),
    )
    client = CoreClient("unused")
    client.store.apply_snapshot(snapshot, 0)
    vm = SidebarSummaryViewModel(client)
    changes: list[None] = []
    vm.on_changed = lambda: changes.append(None)

    assert vm.plan == active_plan
    assert vm.accounts == snapshot.accounts
    assert vm.tabs == snapshot.tabs

    account = snapshot.accounts[0]
    changed_account = msgspec.structs.replace(account, plan_label="Updated plan")
    assert client.store.apply(
        EventEnvelope(
            seq=1,
            at=active_plan.updated_at,
            event=QuotaUpdated(account=changed_account),
        )
    )
    expected_accounts = tuple(
        changed_account if item.id == changed_account.id else item
        for item in snapshot.accounts
    )
    assert vm.accounts == expected_accounts
    assert vm.plan == active_plan
    assert len(changes) == 1

    switched_snapshot = msgspec.structs.replace(
        snapshot,
        active_project=beta_project,
        accounts=expected_accounts,
    )
    client.store.apply_snapshot(switched_snapshot, 1)

    assert vm.plan == beta_plan
    assert vm.accounts == expected_accounts
    assert len(changes) == 2
