"""What a request for a decision looks like in the system's notifications."""

from gi.repository import Gio, GLib

from aconsole.ui.viewmodels.approval_alerts import Alert
from aconsole.ui.viewmodels.approvals_vm import safe_text
from aconsole.ui.views.approvals.messages import _ as message

OPEN_APPROVAL_ACTION = "open-approval"
_DETAILED = f"app.{OPEN_APPROVAL_ACTION}"
_TOOL_LIMIT = 80


def alert_body(alert: Alert) -> str:
    """Names the tab and the tool. The command stays in the app: a notification shows on a locked screen."""
    return f"{alert.tab_title} · {alert.view.agent} · {safe_text(alert.view.tool, _TOOL_LIMIT)}"


def alert_notification(alert: Alert) -> Gio.Notification:
    notification = Gio.Notification.new(message("approvals.alert.title"))
    notification.set_body(alert_body(alert))
    notification.set_priority(Gio.NotificationPriority.HIGH)
    target = GLib.Variant("s", str(alert.tab_id))
    notification.set_default_action_and_target(_DETAILED, target)
    notification.add_button_with_target(message("approvals.alert.open"), _DETAILED, target)
    return notification
