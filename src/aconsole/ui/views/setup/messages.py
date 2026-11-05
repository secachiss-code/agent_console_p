# ruff: noqa: RUF001 -- Russian UI text
"""Setup and preferences translations for the B11 catalog."""

import locale

_MESSAGES = {
    "startup.loading": ("Connecting to core…", "Подключаемся к ядру…"),
    "startup.failed": ("Could not start AConsole", "Не удалось запустить AConsole"),
    "startup.retry": ("Retry", "Повторить"),
    "startup.diagnostics": ("Copy diagnostics", "Скопировать диагностику"),
    "connection.reconnecting": (
        "Connection to core lost. Reconnecting; displayed data may be out of date.",
        "Связь с ядром потеряна. Переподключаемся; данные на экране могут быть устаревшими.",
    ),
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
        "Не удалось завершить шаг. Повторите попытку.",
    ),
    "setup.expired": (
        "Preview expired. Review changes again.", "Срок предпросмотра истёк. Посмотрите изменения заново.",
    ),
    "setup.applied": ("Changes applied", "Изменения применены"),
    "setup.copy_command": ("Copy", "Скопировать"),
    "setup.copied": ("Command copied", "Команда скопирована"),
    "setup.log": ("Logs: {path}", "Журнал: {path}"),
    "setup.no_agents": (
        "No agent is ready. Create a container with the command on the first step, then run setup again.",
        "Ни один агент не готов. Создайте контейнер командой с первого шага и запустите мастер заново.",
    ),
    "setup.check.host_dependencies": ("Host programs", "Программы на компьютере"),
    "setup.check.agent_containers": ("Agent containers", "Контейнеры агентов"),
    "setup.check.agent_login": ("Agent sign-in", "Вход агентов в аккаунт"),
    "setup.check.agent_versions": ("Agent programs", "Программы агентов"),
    "setup.check.storage": ("Notes folder", "Папка заметок"),
    "setup.check.worktree_storage_mounts": ("Folders in containers", "Папки в контейнерах"),
    "setup.check.devtools_graph_tools": ("Code graph tools", "Инструменты графа кода"),
    "setup.check.notifications": ("Notifications", "Уведомления"),
    "setup.reason.host_dependencies": ("Not installed: {detail}. Install it:", "Не установлено: {detail}. Установите:"),
    "setup.reason.agent_containers": (
        "No agent container yet. AConsole does not create them. Create one, for example:",
        "Контейнеров агентов пока нет. AConsole их не создаёт. Создайте, например:",
    ),
    "setup.reason.agent_login": ("Not signed in: {detail}. Sign in:", "Нет входа в аккаунт: {detail}. Войдите:"),
    "setup.reason.agent_versions": (
        "The agent's program does not answer in: {detail}. Open the container and install it:",
        "Программа агента не отвечает в контейнере: {detail}. Откройте контейнер и установите её:",
    ),
    "setup.reason.storage": (
        "The folder does not exist: {detail}. Create it here on the Changes step, or run:",
        "Папки нет: {detail}. Создайте её здесь на шаге «Изменения» или выполните:",
    ),
    "setup.reason.worktree_storage_mounts": (
        "These containers do not see the working folders: {detail}. Copy the container with your home attached:",
        "Эти контейнеры не видят рабочие папки: {detail}. Сделайте копию контейнера с домашней папкой:",
    ),
    "setup.reason.devtools_graph_tools": (
        "Missing in devtools: {detail}. The code graph then covers fewer languages. Install them there:",
        "В контейнере devtools нет: {detail}. Граф кода покроет меньше языков. Установите их там:",
    ),
    "setup.reason.notifications": (
        "Without this there are no desktop notifications about requests. Install:",
        "Без этого не будет системных уведомлений о запросах. Установите:",
    ),
    "startup.starting": ("Starting AConsole…", "Запускаем AConsole…"),
    "startup.log": ("Log: {path}", "Журнал: {path}"),
    "startup.close": ("Close", "Закрыть"),
    "prefs.title": ("Preferences", "Настройки"),
    "prefs.terminal": ("Terminal", "Терминал"),
    "prefs.font": ("Fonts, in order (blank uses the system font)", "Шрифты по порядку (пусто — системный)"),
    "prefs.font_tip": (
        "Several families, separated by commas: a character the first lacks comes from the next",
        "Несколько шрифтов через запятую: символ, которого нет в первом, берётся из следующего",
    ),
    "prefs.font_add": ("Add an installed monospace font", "Добавить установленный моноширинный шрифт"),
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
    "prefs.approvals_inline": ("This window", "В этом окне"),
    "prefs.approvals_native": ("The agent's own prompt", "В диалоге агента"),
    "prefs.approval_hold": ("Seconds to wait for a decision", "Секунд на решение"),
    "prefs.apply": ("Apply", "Применить"),
    "health.title": ("Core health", "Состояние ядра"),
    "health.core": ("Core", "Ядро"),
    "health.ok": ("Working", "Работает"),
    "health.no_database": ("Working without a database", "Работает без базы данных"),
    "health.no_answer": ("Not answering", "Не отвечает"),
    "health.queued": ("Writes waiting", "Записей в очереди"),
    "health.failed": ("Failed writes", "Ошибок записи"),
    "health.commit": ("Last commit, ms", "Последняя запись, мс"),
    "health.answered": ("Requests answered", "Обработано запросов"),
    "health.slowest": ("Slowest request lately, ms", "Самый долгий запрос за последнее время, мс"),
    "health.resyncs": ("Snapshots pulled again", "Повторных снимков"),
    "health.reconnects": ("Reconnections", "Переподключений"),
    "prefs.saved": ("Preferences saved", "Настройки сохранены"),
    "prefs.updated_draft": (
        "Preferences changed elsewhere. Your edits are kept; review them before applying.",
        "Настройки изменились в другом окне. Ваши правки сохранены; проверьте их перед применением.",
    ),
    "prefs.error": (
        "Could not save preferences. Reopen preferences and try again.",
        "Не удалось сохранить настройки. Откройте настройки заново и повторите попытку.",
    ),
    "prefs.load_error": ("Could not load preferences", "Не удалось загрузить настройки"),
}


def _(key: str) -> str:
    language = locale.getlocale()[0] or ""
    return _MESSAGES[key][1 if language.startswith("ru") else 0]


def known(key: str) -> bool:
    return key in _MESSAGES
