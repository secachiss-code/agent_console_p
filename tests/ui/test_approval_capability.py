"""The window answers requests itself, so it asks the core to hold them."""

from aconsole.ui.client.core_client import _UI_CAPABILITIES


def test_ui_claims_inline_approvals() -> None:
    assert "approvals.inline" in _UI_CAPABILITIES
