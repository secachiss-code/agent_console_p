# SEC-1 — приёмка границы hostrun

База проверки: `2f2ea06`. Узел `plan-1/node-2`, fence 3.
Существующее исправление: `c412748` (`AConsole-Node: SEC-1`).

## Результат

`hostrun test hostrun`: **174 passed**, exit 0. Тесты выполнялись в основном
checkout, к которому привязан действующий hostrun. Каталог `tests/hostrun`
сверен через `diff -qr` (кроме `__pycache__`): различий нет.
`tools/hostrun/actions.py` и `server.py` также совпадают с текущим worktree
по SHA-256:

- actions.py: `2fb56cabdd0400576e2b12a98b0feb9527c0c0b6fbd71a72aa7d86b69dc92e42`
- server.py: `ced31588105d8e0cd42033403b723a58b5aaa7fd2aaee7c608f3a1d17e50dbc6`

## Критерии

- `check`, `test` (включая GUI) и оба режима `gate` формируют только host-команды
  `podman start devtools` и `podman exec`. Shell, интерпретатор checkout,
  pytest и phase_gate передаются как аргументы контейнерной команды.
  Это проверяет `test_check_test_and_gate_keep_checkout_code_inside_devtools`.
- HTTP-тест `test_check_names_the_tools` подменяет check.sh маркерным скриптом,
  перехватывает исполнение host argv и подтверждает отсутствие прямого запуска
  скрипта. Сохраняются код возврата, вывод и аудит named action.
- `test_git_diff_does_not_run_repo_diff_drivers` создаёт настоящий временный
  Git-репозиторий с `diff.external` и `diff.evil.textconv`, запускает построенную
  команду и проверяет обычный diff и отсутствие маркера драйвера.
  Пользовательский `git diff --textconv` отклоняется allowlist.
- Набор также проверяет запрет неизвестных actions/аргументов и запуск через
  policy, включая ошибки запуска контейнера.

## Действующая конфигурация и границы

Прочитаны `~/.config/aconsole-hostrun/policy.toml` и пользовательский unit hostrun.
Unit указывает server.py из основного checkout. В действующей policy отличаются
пути worktree и список действий Codex (шире примера); её не меняли.
Для разрешённых check/test/gate маршрутизация контейнерная и не зависит от
перечня прав другого агента. Запреты policy по-прежнему применяются сервером.

Приёмка подтверждает существующую реализацию в исходниках и регрессиях;
сервис не перезапускался, контейнеры не пересоздавались. Доступ devtools к
домашнему каталогу хоста относится к отдельному SEC-7; оставшиеся host-exec
действия — к SEC-3, доверие review gate — к SEC-4. Эти границы не закрыты SEC-1.

Повторная реализация не потребовалась. Отчёт сохраняется отдельным коммитом
приёмки; исходный рабочий коммит SEC-1 — `c412748`.
