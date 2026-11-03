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
    "plan.back": ("Back to terminal", "К терминалу"),  # noqa: RUF001 -- Russian UI text
    "plan.go_tab": ("Go to tab", "Перейти во вкладку"),
    "plan.select_node": ("Select a step to see its details", "Выберите шаг, чтобы увидеть подробности"),
    "plan.depends": ("Depends on: {nodes}", "Зависит от: {nodes}"),
    "plan.blocks": ("Blocks: {nodes}", "Блокирует: {nodes}"),
    "plan.lease": ("Lease until {time}", "Аренда до {time}"),
    "plan.status.pending": ("Waiting", "Ожидает"),
    "plan.status.in_progress": ("In progress", "В работе"),  # noqa: RUF001 -- Russian UI text
    "plan.status.verifying": ("Verifying", "Проверяется"),
    "plan.status.blocked": ("Blocked", "Заблокирован"),
    "plan.status.done": ("Done", "Готово"),
    "plan.status.failed": ("Failed", "Ошибка"),
    "plan.status.cancelled": ("Cancelled", "Отменён"),
    "quota.title": ("Limits", "Лимиты"),
    "quota.title_left": ("Limits left", "Остаток лимитов"),
    "quota.unknown": ("No usage data yet", "Данных о лимитах пока нет"),  # noqa: RUF001 -- Russian UI text
    "quota.unknown.copy": (
        "Usage has not been reported by this agent",
        "Агент ещё не передал данные о лимитах",  # noqa: RUF001 -- Russian UI text
    ),
    "quota.5h": ("5 h", "5 ч"),
    "quota.7d": ("7 d", "7 д"),
    "quota.week": ("Week", "Неделя"),
    "quota.month": ("Month", "Месяц"),
    "quota.budget": ("Pool", "Пул"),
    "quota.auto": ("Auto + Composer", "Auto + Composer"),
    "quota.api": ("API", "API"),
    "quota.empty": ("No data yet", "Пока нет данных"),
    "quota.observed": ("as of {time}", "на {time}"),
    "quota.reset": ("Resets at {time}", "Сброс в {time}"),  # noqa: RUF001 -- Russian UI text
    "quota.tabs": ("Tabs using this account", "Вкладки этого аккаунта"),
    "quota.critical": ("An account has reached 95% of its limit", "Использовано не менее 95 % лимита аккаунта"),
    "quota.details": ("Details", "Подробнее"),
    "quota.source_unknown": ("Usage source unavailable", "Источник данных не указан"),
    "quota.peak": ("up to {pct} %", "до {pct} %"),
    "quota.spent": ("Spent {money}", "Потрачено {money}"),
    "quota.credits": ("Credits left: {count}", "Осталось кредитов: {count}"),
    "quota.credits_unlimited": ("Credits: unlimited", "Кредиты: без ограничений"),
    "quota.api_cost": (
        "Open sessions at API prices: {money} ({count})",
        "Открытые сессии по ценам API: {money} ({count})",
    ),
    "quota.mcp": (
        "AConsole tools sent the agent {size}, {tokens} tokens, in {calls} calls",
        "Инструменты AConsole передали агенту {size}, {tokens} токенов, вызовов: {calls}",
    ),
    "quota.info.mcp": (
        "Tokens for AConsole's tools are estimated as bytes / 4.",
        "Токены инструментов AConsole оценены как байты / 4.",
    ),
    "quota.peak_left": ("{pct} % left", "осталось {pct} %"),
    "quota.refresh": ("Ask every agent for its limits now", "Запросить лимиты у всех агентов сейчас"),  # noqa: RUF001 -- Russian UI text
    "quota.info": ("Where the limits come from", "Откуда берутся лимиты"),
    "quota.info.never": ("No numbers yet", "Чисел пока нет"),
    "quota.info.now": ("Updated just now, at {time}", "Обновлено только что, в {time}"),
    "quota.info.minutes": ("Updated at {time}, {count} min ago", "Обновлено в {time}, {count} мин назад"),
    "quota.info.hours": ("Updated at {time}, {count} h ago", "Обновлено в {time}, {count} ч назад"),
    "quota.info.date": ("Updated on {time}", "Обновлено {time}"),
    "quota.info.every": ("Asked every {minutes} min", "Опрашивается каждые {minutes} мин"),
    "quota.info.note": (
        "The core asks each agent on its own schedule. The refresh button asks all of them now.",
        "Ядро опрашивает каждого агента по своему расписанию. Кнопка обновления спрашивает всех сразу.",
    ),
    "quota.source.none": ("No source has answered yet", "Источник пока не ответил"),
    "quota.source.claude-usage": (
        "Claude's /usage in the agent-claude container",
        "/usage Claude в контейнере agent-claude",
    ),
    "quota.source.codex-app-server": (
        "Codex app server in the agent-codex container",
        "Сервер приложения Codex в контейнере agent-codex",
    ),
    "quota.source.cursor-dashboard": (
        "Cursor's dashboard API, with the CLI's login",
        "API панели Cursor, по входу Cursor CLI",
    ),
    "quota.source.opencode-go": (
        "OpenCode Go usage API, with the CLI's key",
        "API использования OpenCode Go, по ключу OpenCode CLI",
    ),
    "section.collapse": ("Fold", "Свернуть"),
    "section.expand": ("Unfold", "Развернуть"),
    "git.card.title": ("Git", "Git"),
    "git.card.open": ("Git & checkpoints", "Git и чекпоинты"),
    "git.card.unknown": ("Branches, commits and snapshots", "Ветки, коммиты и снимки"),
    "git.card.worktrees": (
        "Worktrees: {count} · with changes: {changed}",
        "Worktree: {count} · с изменениями: {changed}",  # noqa: RUF001 -- Russian UI text
    ),
    "git.card.ready": ("Ready to merge: {count}", "Готово к слиянию: {count}"),
    "git.card.conflicts": ("With conflicts: {count}", "С конфликтами: {count}"),  # noqa: RUF001 -- Russian UI text
    "git.card.nothing": ("Nothing to merge", "Сливать нечего"),
}


def _(key: str) -> str:
    language = locale.getlocale()[0] or ""
    return _MESSAGES[key][1 if language.startswith("ru") else 0]
