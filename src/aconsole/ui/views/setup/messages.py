"""Setup and preferences translations for the B11 catalog."""

import locale

_MESSAGES = {
    "setup.title": ("Welcome to AConsole", "Добро пожаловать в AConsole"),
    "setup.found": ("Found", "Нашли"),
    "setup.project": ("Project", "Проект"),
    "setup.changes": ("Changes", "Изменения"),
    "setup.task": ("First task", "Первая задача"),
    "setup.done": ("Ready", "Готово"),
    "setup.next": ("Continue", "Дальше"),
    "setup.back": ("Back", "Назад"),
    "setup.skip": ("Later", "Позже"),
    "setup.finish": ("Finish", "Готово"),
    "setup.restart": ("Run setup again…", "Запустить мастер заново…"),
    "setup.apply": ("Apply", "Применить"),
    "setup.preview": ("Preview changes", "Посмотреть изменения"),
    "setup.checking": ("Checking your environment…", "Проверяем окружение…"),
    "setup.checked": ("Review the results, then continue", "Проверьте результаты и продолжите"),
    "setup.project_copy": (
        "Agents ask before acting. You can change this in preferences.",
        "Агенты спрашивают, прежде чем действовать. Это можно изменить в настройках.",
    ),
    "setup.folder": ("Choose a project folder…", "Выбрать папку проекта…"),
    "setup.no_project": ("No project selected", "Проект не выбран"),
    "setup.preview_copy": (
        "Review the exact changes before applying them", "Проверьте точные изменения перед применением",
    ),
    "setup.no_changes": ("No changes required", "Изменения не требуются"),
    "setup.task_copy": (
        "Open an agent in a new working copy, or continue without starting a task.",
        "Откройте агента в новой рабочей копии или продолжите без запуска задачи.",
    ),
    "setup.open_task": ("Open first task", "Открыть первую задачу"),
    "setup.done_copy": (
        "You are ready. Review changes and checkpoints from the tab menu.",
        "Всё готово. Дифф и контрольные точки доступны из меню вкладки.",
    ),
    "setup.login": ("Sign-in needed", "Нужен вход"),
    "setup.available": ("Available", "Доступен"),
    "setup.unavailable": ("Unavailable", "Недоступен"),
    "setup.error": (
        "Could not complete this step. Try again.",
        "Не удалось завершить шаг. Повторите попытку.",  # noqa: RUF001 -- Russian UI text
    ),
    "setup.expired": (
        "Preview expired. Review changes again.", "Срок предпросмотра истёк. Посмотрите изменения заново.",
    ),
    "setup.applied": ("Changes applied", "Изменения применены"),
    "prefs.title": ("Preferences", "Настройки"),
    "prefs.terminal": ("Terminal", "Терминал"),
    "prefs.font": ("Font family (blank uses the system font)", "Шрифт (пусто — системный)"),
    "prefs.font_size": ("Font size", "Размер шрифта"),
    "prefs.scrollback": ("Scrollback lines", "Строк прокрутки"),
    "prefs.appearance": ("Appearance", "Внешний вид"),
    "prefs.theme": ("Theme", "Тема"),
    "prefs.quota_display": ("Limits show", "Лимиты: показывать"),
    "prefs.quota_used": ("Used", "Использовано"),
    "prefs.quota_remaining": ("Remaining", "Осталось"),
    "prefs.system": ("System", "Системная"),
    "prefs.light": ("Light", "Светлая"),
    "prefs.dark": ("Dark", "Тёмная"),
    "prefs.approvals": ("Agent requests", "Запросы агентов"),
    "prefs.approvals_mode": ("Answer in", "Отвечать"),
    "prefs.approvals_inline": ("This window", "В этом окне"),  # noqa: RUF001 -- Russian UI text
    "prefs.approvals_native": ("The agent's own prompt", "В диалоге агента"),  # noqa: RUF001 -- Russian UI text
    "prefs.approval_hold": ("Seconds to wait for a decision", "Секунд на решение"),
    "prefs.apply": ("Apply", "Применить"),
    "health.title": ("Core health", "Состояние ядра"),
    "health.core": ("Core", "Ядро"),
    "health.ok": ("Working", "Работает"),
    "health.no_database": ("Working without a database", "Работает без базы данных"),
    "health.no_answer": ("Not answering", "Не отвечает"),  # noqa: RUF001 -- Russian
    "health.queued": ("Writes waiting", "Записей в очереди"),
    "health.failed": ("Failed writes", "Ошибок записи"),
    "health.commit": ("Last commit, ms", "Последняя запись, мс"),
    "health.answered": ("Requests answered", "Обработано запросов"),
    "health.slowest": ("Slowest request lately, ms", "Самый долгий запрос за последнее время, мс"),
    "health.resyncs": ("Snapshots pulled again", "Повторных снимков"),
    "health.reconnects": ("Reconnections", "Переподключений"),
    "prefs.saved": ("Preferences saved", "Настройки сохранены"),
    "prefs.error": (
        "Could not save preferences. Reopen preferences and try again.",
        "Не удалось сохранить настройки. Откройте настройки заново и повторите попытку.",  # noqa: RUF001 -- Russian UI text
    ),
    "prefs.load_error": ("Could not load preferences", "Не удалось загрузить настройки"),  # noqa: RUF001 -- Russian UI text
}


def _(key: str) -> str:
    language = locale.getlocale()[0] or ""
    return _MESSAGES[key][1 if language.startswith("ru") else 0]
