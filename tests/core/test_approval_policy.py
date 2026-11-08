"""Approval policy: who decides, at which level, and who is allowed to change it."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import msgspec
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from aconsole.contracts.app import ProjectApprovalPolicyParams, ProjectView, SettingsUpdateParams
from aconsole.contracts.common import ClientRole, ProjectId
from aconsole.contracts.ipc import ResponseErr, ResponseOk
from aconsole.contracts.settings import Settings, SettingsPatch
from aconsole.core.application.ports.tab_store import ProjectRecord
from aconsole.core.application.services.approval_policy import ApprovalPolicyBook
from aconsole.core.application.services.projects import ProjectCatalog
from aconsole.core.domain.approval_policy import (
    ApprovalPolicyChoice,
    ApprovalPolicyMode,
    DangerousSubmode,
    PolicyOrigin,
    PolicyVerdict,
    verdict,
)
from aconsole.core.domain.errors import DomainError
from aconsole.core.domain.risk import RiskTag
from aconsole.core.infrastructure.persistence.engine import MIGRATIONS, migrate, write_engine
from aconsole.core.infrastructure.persistence.repositories.tabs import TabDatabase
from aconsole.core.infrastructure.persistence.writer import Writer
from tests.core.test_tabs import _call, _ready, _service

if TYPE_CHECKING:
    from pathlib import Path

_WHEN = datetime(2026, 11, 7, tzinfo=UTC)
_TAGS = frozenset({RiskTag.DESTRUCTIVE})


def test_scenario_stub_asks_like_the_default() -> None:
    scenario = ApprovalPolicyChoice(mode=ApprovalPolicyMode.SCENARIO, dangerous=DangerousSubmode.ALLOW)
    asking = ApprovalPolicyChoice()
    assert verdict(scenario, frozenset()) is PolicyVerdict.ASK
    assert verdict(scenario, _TAGS) is PolicyVerdict.ASK
    assert verdict(asking, _TAGS) is PolicyVerdict.ASK


def test_allow_submodes_follow_the_classifier_tags() -> None:
    ask_dangerous = ApprovalPolicyChoice(mode=ApprovalPolicyMode.ALLOW, dangerous=DangerousSubmode.ASK)
    allow_dangerous = ApprovalPolicyChoice(mode=ApprovalPolicyMode.ALLOW, dangerous=DangerousSubmode.ALLOW)
    deny = ApprovalPolicyChoice(mode=ApprovalPolicyMode.DENY, dangerous=DangerousSubmode.ALLOW)
    assert verdict(ask_dangerous, frozenset()) is PolicyVerdict.ALLOW
    assert verdict(ask_dangerous, _TAGS) is PolicyVerdict.ASK
    assert verdict(allow_dangerous, _TAGS) is PolicyVerdict.ALLOW
    assert verdict(deny, frozenset()) is PolicyVerdict.DENY


def test_project_choice_replaces_the_global_one() -> None:
    book = ApprovalPolicyBook()
    book.set_global(
        ApprovalPolicyChoice(mode=ApprovalPolicyMode.DENY),
        origin=PolicyOrigin.UI,
    )
    assert book.set_project(
        "proj",
        ApprovalPolicyChoice(mode=ApprovalPolicyMode.ALLOW, dangerous=DangerousSubmode.ASK),
        origin=PolicyOrigin.UI,
    )
    assert book.decide("proj", frozenset()) is PolicyVerdict.ALLOW
    assert book.decide("proj", _TAGS) is PolicyVerdict.ASK
    assert book.decide("other", frozenset()) is PolicyVerdict.DENY
    assert book.set_project("proj", None, origin=PolicyOrigin.UI)
    assert book.decide("proj", frozenset()) is PolicyVerdict.DENY


def test_mcp_ingest_and_container_cannot_change_the_mode() -> None:
    book = ApprovalPolicyBook()
    for origin in (PolicyOrigin.MCP, PolicyOrigin.INGEST, PolicyOrigin.CONTAINER):
        with pytest.raises(DomainError) as caught:
            book.set_global(ApprovalPolicyChoice(mode=ApprovalPolicyMode.ALLOW), origin=origin)
        assert caught.value.name == "ROLE_FORBIDDEN"
        with pytest.raises(DomainError) as project:
            _ = book.set_project("proj", ApprovalPolicyChoice(mode=ApprovalPolicyMode.DENY), origin=origin)
        assert project.value.name == "ROLE_FORBIDDEN"
    assert book.global_choice == ApprovalPolicyChoice()
    assert book.decide("proj", _TAGS) is PolicyVerdict.ASK


def test_only_the_ui_role_can_write_the_mode() -> None:
    service, _ports = _service()
    connection = _ready(service)
    rev = service.current_settings().rev
    connection.role = ClientRole.CLI
    refused = _call(
        connection,
        2,
        "settings/update",
        SettingsUpdateParams(
            rev=rev,
            patch=SettingsPatch(approval_policy="deny"),
        ),
    )
    assert isinstance(refused, ResponseErr)
    assert refused.error.message == "ROLE_FORBIDDEN"
    assert service.current_settings().approval_policy == "ask"
    themed = _call(
        connection,
        3,
        "settings/update",
        SettingsUpdateParams(
            rev=rev,
            patch=SettingsPatch(theme="dark"),
        ),
    )
    assert isinstance(themed, ResponseOk)
    connection.role = ClientRole.UI
    changed = _call(
        connection,
        4,
        "settings/update",
        SettingsUpdateParams(
            rev=service.current_settings().rev,
            patch=SettingsPatch(approval_policy="allow", approval_dangerous="allow"),
        ),
    )
    assert isinstance(changed, ResponseOk)
    settings = msgspec.json.decode(changed.result, type=Settings)
    assert settings.approval_policy == "allow"
    assert settings.approval_dangerous == "allow"
    assert settings.approvals_mode == "inline"


def test_project_policy_is_ui_only_and_can_inherit_again() -> None:
    service, _ports = _service()

    class _Repos:
        def toplevel(self, path: Path) -> tuple[Path, str] | None:
            return path, "main"

    catalog = ProjectCatalog(_Repos(), new_id=lambda: "proj", now=lambda: _WHEN)
    service.use_projects(catalog)
    assert catalog.add("/repo") is not None
    connection = _ready(service)
    params = ProjectApprovalPolicyParams(project_id=ProjectId("proj"), approval_policy="deny")
    connection.role = ClientRole.CLI
    refused = _call(connection, 2, "project/setApprovalPolicy", params)
    assert isinstance(refused, ResponseErr)
    assert refused.error.message == "ROLE_FORBIDDEN"
    assert catalog.views()[0].approval_policy is None
    connection.role = ClientRole.UI
    changed = _call(connection, 3, "project/setApprovalPolicy", params)
    assert isinstance(changed, ResponseOk)
    view = msgspec.json.decode(changed.result, type=ProjectView)
    assert view.approval_policy == "deny"
    assert view.approval_dangerous == "ask"
    cleared = _call(
        connection,
        4,
        "project/setApprovalPolicy",
        ProjectApprovalPolicyParams(
            project_id=ProjectId("proj"),
            inherit=True,
        ),
    )
    assert isinstance(cleared, ResponseOk)
    assert msgspec.json.decode(cleared.result, type=ProjectView).approval_policy is None


def test_both_levels_survive_a_new_core(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'policy.db'}"
    migrate(url, "head")
    engine = write_engine(url)
    writer = Writer(engine)
    writer.start()
    try:
        store = TabDatabase(writer)
        store.save_project(
            ProjectRecord(
                id="proj",
                name="demo",
                root="/repo",
                default_branch="main",
                created_at=_WHEN,
            )
        )
        service, _ports = _service()
        service.use_store(store)
        _ = service.update_settings(
            SettingsUpdateParams(
                rev=service.current_settings().rev,
                patch=SettingsPatch(approval_policy="allow", approval_dangerous="ask"),
            )
        )
        book = ApprovalPolicyBook()
        book.attach(store)
        assert book.set_project(
            "proj",
            ApprovalPolicyChoice(mode=ApprovalPolicyMode.DENY),
            origin=PolicyOrigin.UI,
        )
        restarted = ApprovalPolicyBook()
        restarted.attach(store)
        fresh, _ports = _service()
        fresh.use_store(store)
        assert fresh.current_settings().approval_policy == "allow"
        assert fresh.current_settings().approval_dangerous == "ask"
        assert restarted.global_choice.mode is ApprovalPolicyMode.ALLOW
        assert restarted.decide("proj", frozenset()) is PolicyVerdict.DENY
        assert restarted.decide(None, _TAGS) is PolicyVerdict.ASK
    finally:
        writer.stop()
        engine.dispose()


def test_migration_downgrade_removes_the_columns_and_upgrade_restores_them(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'migrate.db'}"
    migrate(url, "head")
    assert {"approval_policy", "approval_dangerous"} <= _columns(url)
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url)
    command.downgrade(cfg, "0007_git_ref_deletions")
    assert "projects" in _tables(url)
    assert "approval_policy" not in _columns(url)
    command.upgrade(cfg, "head")
    assert {"approval_policy", "approval_dangerous"} <= _columns(url)


def _columns(url: str) -> set[str]:
    engine = write_engine(url)
    try:
        found = {column["name"] for column in inspect(engine).get_columns("projects")}
    finally:
        engine.dispose()
    return found


def _tables(url: str) -> set[str]:
    engine = write_engine(url)
    try:
        found = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    return found
