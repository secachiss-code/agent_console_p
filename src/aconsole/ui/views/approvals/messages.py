# ruff: noqa: RUF001 -- Russian UI text
"""Bilingual messages of the approval panel, pending the B11 gettext catalog."""

import locale

_MESSAGES = {
    "approvals.count": ("Waiting for your decision: {count}", "Ждут решения: {count}"),
    "approvals.title": ("{tool} · {agent} · {tab}", "{tool} · {agent} · {tab}"),
    "approvals.left": ("{time} left", "Осталось {time}"),
    "approvals.time_up": ("Time is up", "Время вышло"),
    "approvals.cwd": ("In {cwd}", "В {cwd}"),
    "approvals.allow": ("Allow", "Разрешить"),
    "approvals.allow_session": ("Allow in this tab", "Разрешить во вкладке"),
    "approvals.allow_session_tip": (
        "Also allow the same command for the rest of this tab's session",
        "Также разрешить такую команду до конца сессии этой вкладки",
    ),
    "approvals.deny": ("Deny", "Отклонить"),
    "approvals.release": ("Ask in terminal", "Спросить в терминале"),
    "approvals.release_tip": (
        "Leave this request to the agent's own prompt",
        "Передать запрос собственному диалогу агента",
    ),
    "approvals.more_files": ("and {count} more", "и ещё {count}"),
    "approvals.closed": (
        "This request is already closed: your decision was not applied",
        "Запрос уже закрыт: решение не применено",
    ),
    "approvals.failed": (
        "Could not send the decision: {error}. The request is still open",
        "Не удалось отправить решение: {error}. Запрос ещё открыт",
    ),
    "approvals.risk.destructive": ("Destructive", "Удаляет данные"),
    "approvals.risk.outside_worktree": ("Outside the worktree", "Вне рабочего дерева"),
    "approvals.risk.network": ("Network", "Сеть"),
    "approvals.risk.publish": ("Publishes", "Публикация"),
    "approvals.risk.secrets": ("Secrets", "Секреты"),
    "approvals.risk.privilege": ("Elevated rights", "Повышенные права"),
}


def _(key: str) -> str:
    language = locale.getlocale()[0] or ""
    return _MESSAGES[key][1 if language.startswith("ru") else 0]
