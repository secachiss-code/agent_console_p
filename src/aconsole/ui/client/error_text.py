# ruff: noqa: RUF001 -- Russian UI text
"""What a core error means to the person reading it. The core sends only a name and a number."""

from __future__ import annotations

import locale
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aconsole.contracts.errors import ErrorObject

_TEXT: dict[str, tuple[str, str]] = {
    "SERVER_BUSY": ("The core is busy. Try again in a moment", "Ядро занято. Повторите через минуту"),
    "REQUEST_CANCELLED": ("The request was cancelled", "Запрос отменён"),
    "CONTENT_MODIFIED": ("The data changed meanwhile. Try again", "Данные успели измениться. Повторите"),
    "PROJECT_NOT_FOUND": ("This project is no longer known", "Этого проекта больше нет в списке"),
    "PROJECT_NOT_A_GIT_REPO": ("This folder is not a Git repository", "Эта папка не является репозиторием Git"),
    "TAB_NOT_FOUND": ("This tab no longer exists", "Этой вкладки больше нет"),
    "TAB_STATE_CONFLICT": ("The tab has moved on. Try again", "Состояние вкладки изменилось. Повторите"),
    "AGENT_UNAVAILABLE": (
        "This agent is not available: check its container in the setup wizard",
        "Агент недоступен: проверьте его контейнер в мастере настройки",
    ),
    "AGENT_NOT_LOGGED_IN": (
        "This agent is not signed in: sign in inside its container",
        "Агент не вошёл в аккаунт: выполните вход в его контейнере",
    ),
    "CONTAINER_START_FAILED": ("The agent's container did not start", "Контейнер агента не запустился"),
    "WORKTREE_CREATE_FAILED": ("Could not create a working copy", "Не удалось создать рабочую копию"),
    "WORKTREE_DIRTY": ("The working copy has uncommitted changes", "В рабочей копии есть незафиксированные изменения"),
    "BRANCH_ALREADY_CHECKED_OUT": (
        "This branch is open in another working copy", "Эта ветка открыта в другой рабочей копии",
    ),
    "PATH_NOT_ALLOWED": ("This path is outside what the application may use", "Этот путь вне разрешённых приложению"),
    "SESSION_NOT_FOUND": ("This session no longer exists", "Этой сессии больше нет"),
    "APPROVAL_NOT_FOUND": ("This request is already closed", "Этот запрос уже закрыт"),
    "APPROVAL_EXPIRED": ("The time for this request ran out", "Время на этот запрос вышло"),
    "APPROVAL_ALREADY_DECIDED": ("This request was already decided", "По этому запросу уже есть решение"),
    "CHECKPOINT_NOT_FOUND": ("This checkpoint no longer exists", "Этой контрольной точки больше нет"),
    "CHECKPOINT_RESTORE_CONFLICT": ("Files changed since the checkpoint", "Файлы изменились после контрольной точки"),
    "CONFIRM_TOKEN_INVALID": ("The preview is out of date. Review again", "Предпросмотр устарел. Посмотрите ещё раз"),
    "DIFF_TOO_LARGE": ("The changes are too large to show", "Изменения слишком большие для показа"),
    "GIT_COMMAND_FAILED": ("Git could not do this", "Git не смог это выполнить"),
    "GIT_WORKTREE_UNKNOWN": ("This working copy is no longer known", "Этой рабочей копии больше нет в списке"),
    "GIT_BRANCH_UNKNOWN": ("This branch no longer exists", "Этой ветки больше нет"),
    "GIT_SNAPSHOT_STALE": ("The repository changed meanwhile. Try again", "Репозиторий успел измениться. Повторите"),
    "GIT_STATE_STALE": ("The repository changed meanwhile. Try again", "Репозиторий успел измениться. Повторите"),
    "WORKTREE_BUSY": ("An agent is working in this copy", "В этой рабочей копии работает агент"),
    "GIT_OPERATION_IN_PROGRESS": ("Another Git operation is running", "Уже идёт другая операция Git"),
    "MERGE_PREVIEW_STALE": (
        "The branches changed. Review the merge again", "Ветки изменились. Посмотрите слияние ещё раз",
    ),
    "MERGE_CONFLICT": ("The merge has conflicts", "При слиянии есть конфликты"),
    "MERGE_MODE_UNAVAILABLE": ("This way of merging cannot be used here", "Этот способ слияния здесь недоступен"),
    "WORKTREE_NOT_REMOVABLE": ("This working copy cannot be removed", "Эту рабочую копию нельзя удалить"),
    "DATABASE_TRANSACTION_FAILED": ("Could not save to the database", "Не удалось записать в базу данных"),
    "PLAN_NOT_FOUND": ("This plan no longer exists", "Этого плана больше нет"),
    "NODE_NOT_FOUND": ("This step no longer exists", "Этого шага больше нет"),
    "NODE_TRANSITION_INVALID": ("The step cannot change like this", "Шаг нельзя так изменить"),
    "NODE_NOT_READY": ("Steps before this one are not finished", "Предыдущие шаги ещё не закончены"),
    "LEASE_HELD": ("Another agent is working on this step", "Над этим шагом работает другой агент"),
    "NODE_EVIDENCE_REQUIRED": ("This step needs evidence first", "Для этого шага сначала нужны доказательства"),
    "PATHS_OVERLAP": ("Another step is changing the same files", "Те же файлы меняет другой шаг"),
    "VAULT_NOT_CONFIGURED": ("The notes folder is not set up", "Папка заметок не настроена"),
    "VAULT_CONFLICT": ("The note changed meanwhile", "Заметка успела измениться"),
    "NOTE_NOT_FOUND": ("This note no longer exists", "Этой заметки больше нет"),
    "SETUP_FIX_FAILED": ("The fix did not work", "Исправление не сработало"),
    "AGENT_CONFIG_UNSUPPORTED_VERSION": ("This agent version is not supported", "Эта версия агента не поддерживается"),
}
_FALLBACK = (
    "The core could not do this ({name}). Details are in {log}",
    "Ядро не смогло это выполнить ({name}). Подробности в журнале: {log}",
)


def core_log_path() -> Path:
    """`$XDG_STATE_HOME/aconsole/core.log`. The core logs to stderr, and only this run and the one before stay."""
    state = os.environ.get("XDG_STATE_HOME", "")
    return (Path(state) if state else Path.home() / ".local" / "state") / "aconsole" / "core.log"


def error_text(error: ErrorObject) -> str:
    russian = (locale.getlocale()[0] or "").startswith("ru")
    known = _TEXT.get(error.message)
    if known is not None:
        return known[1 if russian else 0]
    return _FALLBACK[1 if russian else 0].format(name=error.message, log=core_log_path())
