"""GTK regression coverage for an interrupted preferences save."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import msgspec

from aconsole.contracts.app import ProjectApprovalPolicyParams, SettingsUpdateParams
from aconsole.contracts.settings import Settings, commit_settings
from aconsole.core.infrastructure.fake.scenarios import demo_snapshot
from aconsole.ui.viewmodels.setup_vm import PreferencesViewModel
from aconsole.ui.views.setup.messages import _ as translate
from aconsole.ui.views.setup.preferences import PreferencesDialog
from tests.gui.front.test_b2_tabs import _model, _run_ui, _TabsTestApp  # pyright: ignore[reportPrivateUsage]
from tests.gui.front.test_preferences_drafts import _answer, _until  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    import pytest


def test_preferences_retry_only_unconfirmed_project_policy(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: PLR0915 -- one dialog across failure and retry
    async def scenario(app: _TabsTestApp) -> None:  # noqa: PLR0915 -- exercise the full save sequence
        client, _tabs = _model(demo_snapshot())
        settings = Settings(rev=1, vault_path="", worktrees_dir="")
        viewmodel = PreferencesViewModel(client)
        project = viewmodel.active_project()
        assert project is not None
        requests: list[str] = []
        release_project = asyncio.Event()

        async def request[T: msgspec.Struct](method: str, params: msgspec.Struct, result_type: type[T]) -> T:
            nonlocal settings
            requests.append(method)
            if method == "settings/update":
                assert isinstance(params, SettingsUpdateParams)
                settings = commit_settings(settings, params.patch)
            if method == "project/setApprovalPolicy":
                assert isinstance(params, ProjectApprovalPolicyParams)
                if requests.count(method) == 1:
                    _ = await release_project.wait()
                    raise RuntimeError
                confirmed = msgspec.structs.replace(project, approval_policy=params.approval_policy)
                return msgspec.convert(confirmed, type=result_type)
            return msgspec.convert(settings, type=result_type)

        monkeypatch.setattr(client, "request", request)
        dialog = PreferencesDialog(viewmodel)
        assert app.window is not None
        app.window.present()
        dialog.open(app.window)
        await _until(lambda: not dialog.loading)
        assert not dialog.save_button.get_sensitive()
        dialog.quota_critical.set_text("101")
        assert not dialog.save_button.get_sensitive()
        assert dialog.status_label.get_text() == translate("prefs.invalid_number")
        dialog.quota_critical.set_text("79")
        assert not dialog.save_button.get_sensitive()
        assert dialog.status_label.get_text() == translate("prefs.quota_threshold_order")
        _ = dialog.close()
        prompt = dialog._close_prompt  # pyright: ignore[reportPrivateUsage]
        assert prompt is not None
        assert not prompt.get_response_enabled("save")
        await _answer(prompt, "cancel")

        dialog.quota_critical.set_text("80")
        dialog.project_policy.set_selected(2)
        assert dialog.save_button.get_sensitive()
        dialog.save()
        await _until(lambda: requests[-1] == "project/setApprovalPolicy")
        assert not dialog.save_button.get_sensitive()
        assert dialog.status_label.get_text() == translate("prefs.saving")
        dialog.save()
        release_project.set()
        assert dialog.worker is not None
        await dialog.worker
        assert dialog.status_label.get_text() == translate("prefs.partial")
        assert settings.quota_critical_pct == 80
        assert dialog.project_policy.get_selected() == 2
        assert dialog.save_button.get_sensitive()
        dialog.save()
        await dialog.worker
        assert not dialog.save_button.get_sensitive()
        assert dialog.status_label.get_text() == translate("prefs.saved")
        assert requests == ["settings/get", "settings/update", "project/setApprovalPolicy", "project/setApprovalPolicy"]
        await client.close()

    _run_ui(scenario)
