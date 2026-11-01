"""Localized messages introduced by the B2 tab list."""

import locale

_MESSAGES = {
    "tabs.list": ("Tabs", "Вкладки"),
    "tabs.name": ("Tab name", "Название вкладки"),
    "tabs.empty": ("Open an agent in a project working copy", "Откройте агента в рабочей копии проекта"),
    "tabs.new": ("New tab", "Новая вкладка"),
    "tabs.menu": ("Tab menu", "Меню вкладки"),
    "tabs.rename": ("Rename", "Переименовать"),
    "tabs.close": ("Close…", "Закрыть…"),
    "tabs.wait_minutes": ("{minutes} min", "{minutes} мин"),
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
