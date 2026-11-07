"""Terminal page messages for the B11 translation catalog."""
# ruff: noqa: RUF001, E501 -- bilingual UI messages kept whole for translation

import locale

_MESSAGES = {
    "terminal.waiting_title": ("Agent added to the session", "Агент добавлен в сессию"),
    "terminal.waiting_body": (
        "Another agent is using this working copy. This tab is waiting. Hand off control to start it, or choose an isolated copy for parallel work.",
        "В этой рабочей копии уже запущен другой агент. Новая вкладка ожидает. Передайте управление для запуска или выберите отдельную копию для параллельной работы.",
    ),
    "terminal.handoff": ("Hand off control", "Передать управление"),
    "terminal.handoff_confirm": (
        "Stop the current agent, verify its processes have exited, save a checkpoint, then start this agent?",
        "Остановить текущего агента, проверить завершение его процессов, сохранить снимок и запустить этого агента?",
    ),
    "terminal.cancel": ("Cancel", "Отмена"),
    "terminal.search": ("Search the terminal", "Поиск в терминале"),
    "terminal.search_previous": ("Previous match", "Предыдущее совпадение"),
    "terminal.search_next": ("Next match", "Следующее совпадение"),
    "terminal.search_close": ("Close search", "Закрыть поиск"),
    "clipboard.copy": ("Copy", "Копировать"),
    "clipboard.paste": ("Paste", "Вставить"),
    "clipboard.image": ("Paste image", "Вставить изображение"),
    "clipboard.select": ("Select all", "Выделить всё"),
    "clipboard.empty": ("No text or image in clipboard", "В буфере нет текста или изображения"),
    "clipboard.large": ("Image exceeds 8 MiB or 32 megapixels", "Изображение больше 8 МиБ или 32 мегапикселей"),
    "clipboard.error": ("Could not paste: {error}", "Не удалось вставить: {error}"),
    "terminal.start": ("Start an agent in your project", "Запустите агента в своём проекте"),
    "terminal.retry": ("Retry starting the session", "Повторить запуск сессии"),
    "terminal.ended": ("Session ended", "Сессия завершена"),
    "terminal.ended_code": ("Session ended with code {code}", "Сессия завершена с кодом {code}"),
    "terminal.resume_unavailable": ("No saved conversation, or this agent does not support resume. Choose a new session.",
                                    "Нет сохранённой сессии или агент не поддерживает продолжение. Выберите новую сессию."),
    "terminal.resume": ("Resume", "Возобновить"),
    "terminal.new": ("New", "Новая"),
    "terminal.close": ("Close", "Закрыть"),
    "terminal.empty": ("Select an agent tab", "Выберите вкладку агента"),
    "terminal.error": (
        "Could not start the session: {error}",
        "Не удалось запустить сессию: {error}",
    ),
    "terminal.report_error": (
        "Could not report session state",
        "Не удалось передать состояние сессии",
    ),
    "terminal.restart_error": (
        "Could not restart the session",
        "Не удалось перезапустить сессию",
    ),
}


def _(key: str) -> str:
    language = locale.getlocale()[0] or ""
    return _MESSAGES[key][1 if language.startswith("ru") else 0]
