"""Terminal page messages for the B11 translation catalog."""

import locale

_MESSAGES = {
    "terminal.search": ("Search the terminal", "Поиск в терминале"),
    "terminal.search_previous": ("Previous match", "Предыдущее совпадение"),
    "terminal.search_next": ("Next match", "Следующее совпадение"),
    "terminal.search_close": ("Close search", "Закрыть поиск"),
    "clipboard.copy": ("Copy", "Копировать"),
    "clipboard.paste": ("Paste", "Вставить"),
    "clipboard.image": ("Paste image", "Вставить изображение"),
    "clipboard.select": ("Select all", "Выделить всё"),
    "clipboard.empty": ("No text or image in clipboard", "В буфере нет текста или изображения"),  # noqa: RUF001 -- Russian
    "clipboard.large": ("Image exceeds 8 MiB or 32 megapixels", "Изображение больше 8 МиБ или 32 мегапикселей"),
    "clipboard.error": ("Could not paste: {error}", "Не удалось вставить: {error}"),  # noqa: RUF001 -- Russian
    "terminal.start": ("Start an agent in your project", "Запустите агента в своём проекте"),
    "terminal.retry": ("Retry starting the session", "Повторить запуск сессии"),
    "terminal.ended": ("Session ended", "Сессия завершена"),
    "terminal.ended_code": ("Session ended with code {code}", "Сессия завершена с кодом {code}"),  # noqa: RUF001 -- Russian
    "terminal.resume": ("Resume", "Возобновить"),
    "terminal.new": ("New", "Новая"),
    "terminal.close": ("Close", "Закрыть"),
    "terminal.empty": ("Select an agent tab", "Выберите вкладку агента"),
    "terminal.error": (
        "Could not start the session: {error}",
        "Не удалось запустить сессию: {error}",  # noqa: RUF001 -- Russian UI text
    ),
    "terminal.report_error": (
        "Could not report session state",
        "Не удалось передать состояние сессии",  # noqa: RUF001 -- Russian UI text
    ),
    "terminal.restart_error": (
        "Could not restart the session",
        "Не удалось перезапустить сессию",  # noqa: RUF001 -- Russian UI text
    ),
}


def _(key: str) -> str:
    language = locale.getlocale()[0] or ""
    return _MESSAGES[key][1 if language.startswith("ru") else 0]
