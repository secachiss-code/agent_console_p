"""Bilingual sidebar summary messages, pending the B11 gettext catalog."""

import locale

_MESSAGES = {
    "plan.card.title": ("Plan", "План"),
    "plan.card.progress": ("{done} of {total} done", "Выполнено {done} из {total}"),
    "plan.card.unknown": ("No plan yet", "Плана пока нет"),
    "plan.empty": (
        "Ask the agent to plan the task. The plan appears here and in Obsidian",
        "Попросите агента спланировать задачу — план появится здесь и в Obsidian",
    ),
    "plan.ready": ("Ready: {nodes}", "Готово к работе: {nodes}"),
    "plan.page.soon": (
        "The full plan view comes in a later version",
        "Полная страница плана появится в следующей версии",
    ),
    "quota.title": ("Limits", "Лимиты"),
    "quota.unknown": ("No usage data yet", "Данных о лимитах пока нет"),  # noqa: RUF001 -- Russian UI text
    "quota.unknown.copy": (
        "Usage has not been reported by this agent",
        "Агент ещё не передал данные о лимитах",  # noqa: RUF001 -- Russian UI text
    ),
    "quota.5h": ("5 h", "5 ч"),
    "quota.7d": ("7 d", "7 д"),
    "quota.week": ("Week", "Неделя"),
    "quota.month": ("Month", "Месяц"),
    "quota.budget": ("Budget", "Бюджет"),
    "quota.observed": ("As of {time}", "По данным на {time}"),
    "quota.reset": ("Resets at {time}", "Сброс в {time}"),  # noqa: RUF001 -- Russian UI text
    "quota.tabs": ("Tabs using this account", "Вкладки этого аккаунта"),
    "quota.critical": ("An account has reached 95% of its limit", "Использовано не менее 95 % лимита аккаунта"),
    "quota.details": ("Details", "Подробнее"),
}


def _(key: str) -> str:
    language = locale.getlocale()[0] or ""
    return _MESSAGES[key][1 if language.startswith("ru") else 0]
