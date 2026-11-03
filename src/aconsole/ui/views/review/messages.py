# ruff: noqa: RUF001 -- Russian UI text
"""Bilingual messages of the review page, pending the B11 gettext catalog."""

import locale

_MESSAGES = {
    "git.title": ("Git & checkpoints", "Git и чекпоинты"),
    "git.open": ("Git & checkpoints", "Git и чекпоинты"),
    "git.back": ("Back to terminal", "К терминалу"),
    "git.no_project": ("Add a project first", "Сначала добавьте проект"),
    "git.scope_all": ("All branches", "Все ветки"),
    "git.scope_tab": ("Tab: {title}", "Вкладка: {title}"),
    "git.empty": ("No commits yet", "Коммитов пока нет"),
    "git.select": (
        "Select a commit, a snapshot or uncommitted files",
        "Выберите коммит, снимок или незакоммиченные файлы",
    ),
    "git.more": ("{count} commits loaded", "Загружено коммитов: {count}"),
    "git.uncommitted": ("Uncommitted: {branch}", "Не закоммичено: {branch}"),
    "git.uncommitted_counts": (
        "{staged} staged · {unstaged} changed · {untracked} new · {conflicted} in conflict",
        "в индексе {staged} · изменено {unstaged} · новых {untracked} · в конфликте {conflicted}",
    ),
    "git.detached": ("detached HEAD", "отсоединённый HEAD"),
    "git.closed_tab": ("closed tab", "вкладка закрыта"),
    "git.mode_worktree": ("Not staged", "Не в индексе"),
    "git.mode_index": ("Staged", "В индексе"),
    "git.mode_base": ("Against base", "Относительно базы"),
    "git.badge_ready": ("Merges +{insertions} −{deletions}", "Сливается +{insertions} −{deletions}"),
    "git.badge_conflict": ("Conflict: {count}", "Конфликт: {count}"),
    "git.badge_behind": ("Behind {count}", "Отстаёт: {count}"),
    "git.badge_unchecked": ("Not checked", "Не проверено"),
    "git.badge_in_progress": ("Operation in progress", "Идёт Git-операция"),
    "git.badge_busy": ("Agent is working", "Агент работает"),
    "git.snapshot": ("Snapshot {turn}: {label}", "Снимок {turn}: {label}"),
    "git.stale": ("Git moved. Reloading", "Git изменился. Обновляю"),
    "review.open": ("Changes and checkpoints", "Изменения и чекпоинты"),
    "review.back": ("Back to terminal", "К терминалу"),
    "review.no_tab": ("Select a tab first", "Сначала выберите вкладку"),
    "review.changes": ("Changes", "Изменения"),
    "review.checkpoints": ("Checkpoints", "Чекпоинты"),
    "review.no_changes": ("No changes", "Изменений нет"),
    "review.select_file": ("Select a file", "Выберите файл"),
    "review.binary": ("Binary file: nothing to show", "Двоичный файл: показать нечего"),
    "review.truncated": ("The diff is cut off here", "Дальше diff обрезан"),
    "review.too_large": ("This diff is too large to show", "Этот diff слишком большой, показать его нельзя"),
    "review.load_error": ("Could not load: {error}", "Не удалось загрузить: {error}"),
    "review.comment": ("Comment", "Комментарий"),
    "review.comment_heading": ("Comment for the agent", "Комментарий для агента"),
    "review.comment_body": ("{path}, {hunk}", "{path}, {hunk}"),
    "review.comment_send": ("Send", "Отправить"),
    "review.comment_cancel": ("Cancel", "Отмена"),
    "review.comment_sent": ("Comment sent", "Комментарий отправлен"),
    "review.comment_error": ("Could not send the comment", "Не удалось отправить комментарий"),
    "review.no_checkpoints": (
        "A checkpoint appears with each prompt you send",
        "Чекпоинт появляется при каждом отправленном запросе",
    ),
    "review.checkpoint_line": (
        "Turn {turn} · {time} · {files} files · +{insertions} −{deletions}",
        "Ход {turn} · {time} · файлов: {files} · +{insertions} −{deletions}",
    ),
    "review.restore": ("Restore", "Восстановить"),
    "review.restore_heading": ("Restore this checkpoint?", "Восстановить этот чекпоинт?"),
    "review.restore_body": (
        "{scope}\n\nFiles in the checkpoint: {count}\n{files}\n\nThe current state is kept as a new checkpoint.",
        "{scope}\n\nФайлов в чекпоинте: {count}\n{files}\n\nТекущее состояние сохранится как новый чекпоинт.",
    ),
    "review.restore_more": ("and {count} more", "и ещё {count}"),
    "review.restore_cancel": ("Cancel", "Отмена"),
    "review.restored": ("Restored", "Восстановлено"),
    "review.restore_busy": (
        "The agent is working. Restore when it stops",
        "Агент работает. Восстановите, когда он закончит",
    ),
    "review.restore_expired": ("The confirmation expired. Try again", "Подтверждение устарело. Попробуйте ещё раз"),
    "review.restore_error": ("Could not restore: {error}", "Не удалось восстановить: {error}"),
}


def _(key: str) -> str:
    language = locale.getlocale()[0] or ""
    return _MESSAGES[key][1 if language.startswith("ru") else 0]
