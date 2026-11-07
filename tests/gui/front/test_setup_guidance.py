"""Setup guidance must not recommend exposing HOME or hide the isolation check behind English IDs."""
import pytest

from aconsole.contracts.app import CheckResult
from aconsole.ui.views.setup import messages
from aconsole.ui.views.setup.wizard import _check_reason, _check_title


@pytest.mark.parametrize("language", ["en_US", "ru_RU"])
@pytest.mark.parametrize("category", ["agent_containers", "worktree_storage_mounts", "container_isolation"])
def test_isolated_container_guidance_is_localized_and_safe(
    monkeypatch: pytest.MonkeyPatch, language: str, category: str,
) -> None:
    monkeypatch.setattr(messages.locale, "getlocale", lambda: (language, "UTF-8"))
    check = CheckResult(
        id=category, title="UNTRANSLATED", ok=False, severity="warning", detail="agent-example",
        fix_id=None, requires_confirmation=False, command="podman inspect agent-example",
    )
    title = _check_title(check)
    reason = _check_reason(check)
    assert title != "UNTRANSLATED"
    assert reason != check.detail
    assert "$HOME:$HOME" not in reason
    assert "home attached" not in reason
    assert "с домашней папкой" not in reason  # noqa: RUF001 -- Russian regression phrase
    if category != "agent_containers":
        assert "agent-example" in reason
    if category in {"agent_containers", "container_isolation"}:
        assert "README" in reason
