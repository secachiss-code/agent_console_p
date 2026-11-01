"""Command palette messages for the B11 catalog."""

import locale

_MESSAGES = {
    "palette.title": ("Commands", "Команды"),
    "palette.search": ("Search commands", "Найти команду"),
    "palette.back": ("Back", "Назад"),
    "palette.loading": ("Loading commands…", "Загрузка команд…"),
    "palette.empty": ("No matching commands", "Подходящих команд нет"),
    "palette.recent_empty": ("Type to find a command", "Начните вводить команду"),
    "palette.error": ("Could not load commands", "Не удалось загрузить команды"),  # noqa: RUF001 -- Russian UI text
    "palette.execute_error": (
        "Could not execute the command",
        "Не удалось выполнить команду",  # noqa: RUF001 -- Russian UI text
    ),
    "palette.run": ("Run", "Выполнить"),
    "palette.uri_error": ("Could not open the link", "Не удалось открыть ссылку"),  # noqa: RUF001 -- Russian UI text
    "palette.kind.command": ("Commands", "Команды"),
    "palette.kind.native_skill": ("Skills", "Скиллы"),
    "palette.kind.plan_node": ("Plan", "План"),
    "palette.kind.note": ("Notes", "Заметки"),
    "palette.kind.tab": ("Tabs", "Вкладки"),
    "palette.kind.session": ("Sessions", "Сессии"),
    "palette.kind.action": ("Actions", "Действия"),
    "palette.kind.approval": ("Approvals", "Подтверждения"),
}


def _(key: str) -> str:
    language = locale.getlocale()[0] or ""
    return _MESSAGES[key][1 if language.startswith("ru") else 0]
