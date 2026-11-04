"""Localized messages introduced by the B2 tab list."""

import locale

_MESSAGES = {
    "tabs.list": ("Tabs", "Вкладки"),
    "tabs.name": ("Tab name", "Название вкладки"),
    "tabs.empty": ("Open an agent in a project working copy", "Откройте агента в рабочей копии проекта"),
    "tabs.project": ("Project", "Проект"),
    "tabs.search": ("Search sessions", "Поиск сессий"),
    "tabs.search.none": ("No sessions match", "Сессии не найдены"),
    "tabs.new": ("New tab", "Новая вкладка"),
    "tabs.menu": ("Tab menu", "Меню вкладки"),
    "tabs.rename": ("Rename", "Переименовать"),
    "tabs.close": ("Close…", "Закрыть…"),
    "tabs.wait_minutes": ("{minutes} min", "{minutes} мин"),
    "tabs.wait_hours": ("{hours} h", "{hours} ч"),
    "tabs.wait_days": ("{days} d", "{days} д"),
    "tabs.new.agent": ("Agent", "Агент"),
    "tabs.new.project": ("Project", "Проект"),
    "tabs.new.add_project": ("Add project…", "Добавить проект…"),
    "tabs.new.open": ("Open", "Открыть"),
    "tabs.new.no_agents": ("No agents are available", "Нет доступных агентов"),
    "tabs.new.no_projects": ("Add a project to open a tab", "Добавьте проект, чтобы открыть вкладку"),
    "tabs.new.error": ("Could not open the tab: {error}", "Не удалось открыть вкладку: {error}"),  # noqa: RUF001 -- Russian UI text
    "tabs.new.project_error": ("Could not add the project: {error}", "Не удалось добавить проект: {error}"),  # noqa: RUF001 -- Russian UI text
    "tabs.close_error": ("Could not close the tab: {error}", "Не удалось закрыть вкладку: {error}"),  # noqa: RUF001 -- Russian UI text
    "tabs.close_heading": ("Keep the working copy?", "Оставить рабочую копию?"),
    "tabs.close_body": ("The tab has its own working copy", "У вкладки есть своя рабочая копия"),  # noqa: RUF001 -- Russian UI text
    "tabs.close_cancel": ("Cancel", "Отмена"),
    "tabs.close_keep": ("Keep", "Оставить"),
    "tabs.close_remove": ("Remove", "Удалить"),
    "tabs.close_kept": (
        "Closed the tab and kept the working copy ({reason})",
        "Вкладка закрыта, рабочая копия осталась ({reason})",
    ),
    "tabs.reorder_error": (
        "Could not finish reordering. Tab order may have changed",
        "Не удалось завершить перестановку. Порядок вкладок мог измениться",  # noqa: RUF001 -- Russian UI text
    ),
    "tabs.status.starting": ("Starting", "Запускается"),
    "tabs.status.idle": ("Idle", "Свободен"),
    "tabs.status.working": ("Working", "Работает"),
    "tabs.status.waiting_approval": ("Waiting for approval", "Ждёт подтверждения"),
    "tabs.status.waiting_input": ("Waiting for input", "Ждёт ввода"),
    "tabs.status.error": ("Error", "Ошибка"),
    "tabs.status.exited": ("Finished", "Завершён"),
    "tabs.status.unknown": ("Unknown status", "Статус неизвестен"),
}


def _(key: str) -> str:
    language = locale.getlocale()[0] or ""
    return _MESSAGES[key][1 if language.startswith("ru") else 0]
