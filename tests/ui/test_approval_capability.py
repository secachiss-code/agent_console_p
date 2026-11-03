"""The window has no approval screen, so it must not ask the core to hold requests for one."""

from aconsole.ui.client.core_client import _UI_CAPABILITIES


def test_ui_does_not_claim_inline_approvals() -> None:
    assert "approvals.inline" not in _UI_CAPABILITIES
