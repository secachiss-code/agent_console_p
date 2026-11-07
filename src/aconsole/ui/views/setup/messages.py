# ruff: noqa: RUF001 -- Russian UI text
"""Setup and preferences translations for the B11 catalog."""

import locale

_MESSAGES = {
    "prefs.quota_warn": ("Warning at used quota, %", "Предупреждение при использовании, %"),
    "prefs.quota_critical": ("Critical at used quota, %", "Критический уровень при использовании, %"),
    "prefs.unsaved": ("Unsaved preferences", "Несохранённые настройки"),
    "prefs.close_body": ("Apply your changes before closing?", "Применить изменения перед закрытием?"),
    "prefs.keep_editing": ("Keep editing", "Продолжить редактирование"),
    "prefs.discard": ("Discard changes", "Отменить изменения"),
    "prefs.saving": ("Saving preferences…", "Сохранение настроек…"),
    "quit.heading": ("Close AConsole?", "Закрыть AConsole?"),
    "quit.counts": ("Running agents: {agents}. Pending requests: {approvals}.",
                    "Работающих агентов: {agents}. Ожидающих запросов: {approvals}."),
    "quit.unknown": ("Could not check running agents. They may still be working.",
                     "Не удалось проверить агентов. Они могут продолжать работу."),
    "quit.effect": ("Work may be interrupted. Pending requests will no longer be visible. Working copies remain.",
                    "Работа может прерваться. Запросы больше не будут видны. Рабочие копии сохранятся."),
    "quit.cancel": ("Stay", "Остаться"),
    "quit.close": ("Close application", "Закрыть приложение"),
    "health.isolation": ("Container isolation", "Изоляция контейнеров"),
    "health.isolation_unknown": (
        "Isolation has not been verified. Check container mounts before trusting approval restrictions.",
        "Изоляция не проверена. Проверьте монтирования контейнеров перед использованием ограничений согласований.",
    ),
    "health.isolation_loading": ("Checking container mounts…", "Проверяем монтирования контейнеров…"),
    "health.isolation_check": ("Check container mounts", "Проверить монтирования контейнеров"),
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
        "No agent is ready. Configure an isolated container using README, then run setup again.",
        "Ни один агент не готов. Настройте изолированный контейнер по README и запустите мастер заново.",
    ),
    "setup.check.host_dependencies": ("Host programs", "Программы на компьютере"),
    "setup.check.agent_containers": ("Agent containers", "Контейнеры агентов"),
    "setup.check.agent_login": ("Agent sign-in", "Вход агентов в аккаунт"),
    "setup.check.agent_versions": ("Agent programs", "Программы агентов"),
    "setup.check.storage": ("Notes folder", "Папка заметок"),
    "setup.check.worktree_storage_mounts": ("Folders in containers", "Папки в контейнерах"),
    "setup.check.devtools_graph_tools": ("Code graph tools", "Инструменты графа кода"),
    "setup.check.container_isolation": ("Container isolation", "Изоляция контейнеров"),
    "setup.check.notifications": ("Notifications", "Уведомления"),
    "setup.reason.host_dependencies": ("Not installed: {detail}. Install it:", "Не установлено: {detail}. Установите:"),
    "setup.reason.agent_containers": (
        "No agent container yet. Choose isolated mounts using README before creating one. Command reference:",
        "Контейнеров агентов пока нет. Перед созданием выберите изолированные папки по README. Справка по команде:",
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
        "Containers cannot see working folders: {detail}. Inspect mounts; provide only worktrees and vault:",
        "Контейнеры не видят рабочие папки: {detail}. Проверьте монтирования; оставьте только рабочие копии и vault:",
    ),
    "setup.reason.devtools_graph_tools": (
        "Missing in devtools: {detail}. The code graph then covers fewer languages. Install them there:",
        "В контейнере devtools нет: {detail}. Граф кода покроет меньше языков. Установите их там:",
    ),
    "setup.reason.container_isolation": (
        ("Host data may be exposed, or isolation is unknown. Use a separate HOME and only worktrees/vault. "
        "See README: Container isolation. Findings: {detail}"),
        ("Данные хоста могут быть доступны, либо изоляция неизвестна. Нужен отдельный HOME, "
        "доступ только к рабочим копиям и vault. См. README: Container isolation. Результат: {detail}"),
    ),
    "setup.reason.notifications": (
        "Desktop notifications need notify-send and a desktop D-Bus session. Missing: {detail}. Check or install:",
        "Для уведомлений нужны notify-send и сеанс D-Bus. Недоступно: {detail}. Проверьте или установите:",
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
        "Could not save preferences. Your edits are kept; try applying again.",
        "Не удалось сохранить настройки. Ваши правки сохранены; повторите применение.",
    ),
    "prefs.load_error": ("Could not load preferences", "Не удалось загрузить настройки"),
}


def _(key: str) -> str:
    language = locale.getlocale()[0] or ""
    return _MESSAGES[key][1 if language.startswith("ru") else 0]


def known(key: str) -> bool:
    return key in _MESSAGES
