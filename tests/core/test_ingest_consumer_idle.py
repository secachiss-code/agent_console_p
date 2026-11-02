"""Which Claude notifications ask for the user's attention."""

from __future__ import annotations

from aconsole.core.infrastructure.http.consumer import _event_name  # pyright: ignore[reportPrivateUsage]
from aconsole.core.infrastructure.http.routes import IngestItem


def _notification(kind: str) -> IngestItem:
    body = f'{{"session_id":"s","hook_event_name":"Notification","notification_type":"{kind}"}}'.encode()
    return IngestItem(tab_id="tab", kind="claude.hook", body=body)


def test_idle_reminder_does_not_move_the_tab() -> None:
    assert _event_name(_notification("idle_prompt")) is None
    assert _event_name(_notification("permission_prompt")) == "Notification"
